import hashlib
import json
import math
import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import create_async_engine

from incidentops.durable.schema import (
    batches,
    chunks,
    embedding_cache,
    events,
    generations,
    heads,
    jobs,
    metadata,
    outbox,
    versions,
)
from incidentops.models import Chunk, Principal, Question, SourceDocument
from incidentops.security import redact


class Conflict(ValueError):
    pass


class LostLease(RuntimeError):
    pass


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Repository:
    def __init__(self, url: str, *, clock=time.time):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.clock = clock

    async def initialize_for_tests(self):
        if self.engine.dialect.name != "sqlite":
            raise ValueError("Use Alembic migrations for PostgreSQL")
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def aclose(self):
        await self.engine.dispose()

    async def _ignore(self, conn, table, values, keys):
        builder = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        await conn.execute(
            builder(table).values(**values).on_conflict_do_nothing(index_elements=keys)
        )

    async def _emit(self, conn, job_id, kind):
        await conn.execute(
            insert(outbox).values(
                id=str(uuid4()),
                job_id=job_id,
                kind=kind,
                created_at=self.clock(),
                published_at=None,
            )
        )

    async def _event(self, conn, job_id, event_type, payload=None):
        sequence = (
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(event_sequence=jobs.c.event_sequence + 1)
                .returning(jobs.c.event_sequence)
            )
        ).scalar_one()
        await conn.execute(
            insert(events).values(
                job_id=job_id, sequence=sequence, event_type=event_type, payload=payload or {}
            )
        )

    async def job_events(self, actor, job_id, after=0):
        if await self.get_job(actor, job_id) is None:
            return None
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(events)
                        .where(events.c.job_id == job_id, events.c.sequence > after)
                        .order_by(events.c.sequence)
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def submit(
        self,
        actor: Principal,
        kind: str,
        key: str,
        payload: dict,
        *,
        deadline_seconds: int = 300,
        request_identity: dict | None = None,
    ):
        if kind not in {"ingest", "answer", "delete"} or not 1 <= len(key) <= 160:
            raise ValueError("Invalid job kind or idempotency key")
        if not 1 <= deadline_seconds <= 3600:
            raise ValueError("Invalid job deadline")
        now = self.clock()
        # The use case redacts source/question text before this boundary. Opaque
        # object version IDs must remain byte-for-byte stable for exact retrieval.
        safe_payload = json.loads(json.dumps(payload))
        digest = fingerprint(
            {
                "kind": kind,
                "payload": request_identity if request_identity is not None else safe_payload,
            }
        )
        value = dict(
            id=str(uuid4()),
            tenant_id=actor.tenant_id,
            principal_id=actor.principal_id,
            groups=list(actor.groups),
            kind=kind,
            idempotency_key=key,
            request_hash=digest,
            payload=safe_payload,
            status="queued",
            attempt=0,
            fence=0,
            lease_until=0,
            deadline=now + deadline_seconds,
            run_after=now,
            created_at=now,
            result=None,
            error_code=None,
        )
        async with self.engine.begin() as conn:
            await self._ignore(conn, jobs, value, ["tenant_id", "principal_id", "idempotency_key"])
            row = (
                (
                    await conn.execute(
                        select(jobs).where(
                            jobs.c.tenant_id == actor.tenant_id,
                            jobs.c.principal_id == actor.principal_id,
                            jobs.c.idempotency_key == key,
                        )
                    )
                )
                .mappings()
                .one()
            )
            if row["request_hash"] != digest:
                raise Conflict("Idempotency key already represents a different request")
            if row["id"] == value["id"]:
                await self._emit(conn, row["id"], kind)
                await self._event(conn, row["id"], "queued")
            return dict(row)

    async def get_job(self, actor: Principal, job_id: str):
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(jobs).where(
                            jobs.c.id == job_id,
                            jobs.c.tenant_id == actor.tenant_id,
                            jobs.c.principal_id == actor.principal_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def cancel(self, actor: Principal, job_id: str):
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.tenant_id == actor.tenant_id,
                    jobs.c.principal_id == actor.principal_id,
                    jobs.c.status.in_(["queued", "retry", "running"]),
                )
                .values(status="cancelled", lease_until=0)
            )
            if result.rowcount == 1:
                await self._event(conn, job_id, "cancelled")
            return result.rowcount == 1

    async def pending_events(self, limit=100):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(outbox, jobs.c.payload.label("job_payload"))
                        .join(jobs, jobs.c.id == outbox.c.job_id)
                        .where(
                            outbox.c.published_at.is_(None),
                            jobs.c.status.in_(["queued", "retry"]),
                            jobs.c.run_after <= self.clock(),
                            jobs.c.deadline > self.clock(),
                        )
                        .order_by(outbox.c.created_at, outbox.c.id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            return [
                dict(row)
                | {
                    "queue": row["job_payload"].get(
                        "queue", "investigations" if row["kind"] == "answer" else "ingestion.small"
                    )
                }
                for row in rows
            ]

    async def mark_event_published(self, event_id):
        async with self.engine.begin() as conn:
            await conn.execute(
                update(outbox).where(outbox.c.id == event_id).values(published_at=self.clock())
            )

    async def claim(self, job_id, *, lease_seconds=60, max_attempts=3):
        now = self.clock()
        async with self.engine.begin() as conn:
            expired = (
                (
                    await conn.execute(
                        update(jobs)
                        .where(
                            jobs.c.id == job_id,
                            jobs.c.deadline <= now,
                            jobs.c.status.in_(["queued", "retry", "running"]),
                        )
                        .values(status="expired", lease_until=0)
                        .returning(jobs.c.id)
                    )
                )
                .scalars()
                .all()
            )
            for identifier in expired:
                await self._event(conn, identifier, "expired")
            row = (
                (
                    await conn.execute(
                        update(jobs)
                        .where(
                            jobs.c.id == job_id,
                            jobs.c.deadline > now,
                            jobs.c.run_after <= now,
                            jobs.c.attempt < max_attempts,
                            or_(
                                jobs.c.status.in_(["queued", "retry"]),
                                and_(jobs.c.status == "running", jobs.c.lease_until <= now),
                            ),
                        )
                        .values(
                            status="running",
                            attempt=jobs.c.attempt + 1,
                            fence=jobs.c.fence + 1,
                            lease_until=now + lease_seconds,
                        )
                        .returning(*jobs.c)
                    )
                )
                .mappings()
                .first()
            )
            if row:
                await self._event(conn, job_id, "running", {"attempt": row["attempt"]})
            return dict(row) if row else None

    async def _owned(self, conn, claim):
        # A conditional UPDATE locks the job row (and SQLite writer), closing the
        # check-then-write race with a new owner, cancellation or lease recovery.
        row = await conn.execute(
            update(jobs)
            .where(
                jobs.c.id == claim["id"],
                jobs.c.tenant_id == claim["tenant_id"],
                jobs.c.fence == claim["fence"],
                jobs.c.status == "running",
                jobs.c.lease_until > self.clock(),
                jobs.c.deadline > self.clock(),
            )
            .values(lease_until=jobs.c.lease_until)
        )
        if row.rowcount != 1:
            raise LostLease("Job lease lost, expired or cancelled")

    async def heartbeat(self, claim, lease_seconds=60):
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == claim["id"])
                .values(lease_until=self.clock() + lease_seconds)
            )

    async def complete(self, claim, result):
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == claim["id"])
                .values(status="succeeded", result=redact(result), lease_until=0)
            )
            await self._event(conn, claim["id"], "succeeded")

    async def retry_or_fail(self, claim, error_code, *, transient=False, delay=1, max_attempts=3):
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            retry = (
                transient
                and claim["attempt"] < max_attempts
                and self.clock() + delay < claim["deadline"]
            )
            status = "retry" if retry else "failed"
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == claim["id"])
                .values(
                    status=status,
                    error_code=error_code,
                    lease_until=0,
                    run_after=self.clock() + delay,
                )
            )
            await self._event(conn, claim["id"], status, {"error_code": error_code})
            if retry:
                await self._emit(conn, claim["id"], claim["kind"])

    async def recover_expired(self, max_attempts=3):
        now = self.clock()
        async with self.engine.begin() as conn:
            expired = (
                (
                    await conn.execute(
                        update(jobs)
                        .where(
                            jobs.c.status.in_(["running", "retry", "queued"]),
                            jobs.c.deadline <= now,
                        )
                        .values(status="expired", lease_until=0)
                        .returning(jobs.c.id)
                    )
                )
                .scalars()
                .all()
            )
            for identifier in expired:
                await self._event(conn, identifier, "expired")
            exhausted = (
                (
                    await conn.execute(
                        update(jobs)
                        .where(
                            jobs.c.status == "running",
                            jobs.c.lease_until <= now,
                            jobs.c.attempt >= max_attempts,
                        )
                        .values(status="failed", error_code="attempts_exhausted")
                        .returning(jobs.c.id)
                    )
                )
                .scalars()
                .all()
            )
            for identifier in exhausted:
                await self._event(conn, identifier, "failed", {"error_code": "attempts_exhausted"})
            recovered = (
                await conn.execute(
                    update(jobs)
                    .where(
                        jobs.c.status == "running",
                        jobs.c.lease_until <= now,
                        jobs.c.deadline > now,
                        jobs.c.attempt < max_attempts,
                    )
                    .values(status="queued", lease_until=0)
                    .returning(jobs.c.id, jobs.c.kind)
                )
            ).all()
            for job_id, kind in recovered:
                await self._emit(conn, job_id, kind)
                await self._event(conn, job_id, "recovered")
            # A broker delivery can exhaust its transport retry before claiming.
            # Reconcile old accepted jobs with no pending outbox, bounded per sweep.
            last_dispatch = (
                select(func.max(outbox.c.created_at))
                .where(outbox.c.job_id == jobs.c.id)
                .scalar_subquery()
            )
            pending_dispatch = (
                select(outbox.c.id)
                .where(outbox.c.job_id == jobs.c.id, outbox.c.published_at.is_(None))
                .exists()
            )
            unclaimed = (
                await conn.execute(
                    select(jobs.c.id, jobs.c.kind)
                    .where(
                        jobs.c.status.in_(["queued", "retry"]),
                        jobs.c.run_after <= now,
                        jobs.c.deadline > now,
                        last_dispatch < now - 60,
                        ~pending_dispatch,
                    )
                    .limit(100)
                )
            ).all()
            for job_id, kind in unclaimed:
                await self._emit(conn, job_id, kind)
            return len(recovered)

    async def head_revision(self, actor, document_id):
        async with self.engine.connect() as conn:
            revision = (
                await conn.execute(
                    select(heads.c.revision).where(
                        heads.c.tenant_id == actor.tenant_id, heads.c.document_id == document_id
                    )
                )
            ).scalar_one_or_none()
            return revision or 0

    async def begin_generation(
        self, claim, document: SourceDocument, embedding_id, *, expected_revision
    ):
        if document.tenant_id != claim["tenant_id"] or document.access_group not in claim["groups"]:
            raise Conflict("Document ownership or access mismatch")
        digest = hashlib.sha256(redact(document.text).encode()).hexdigest()
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            await self._write_access(conn, claim, document.document_id)
            await self._ignore(
                conn,
                versions,
                dict(
                    tenant_id=document.tenant_id,
                    document_id=document.document_id,
                    version=document.version,
                    content_hash=digest,
                ),
                ["tenant_id", "document_id", "version"],
            )
            stored = (
                await conn.execute(
                    select(versions.c.content_hash).where(
                        versions.c.tenant_id == document.tenant_id,
                        versions.c.document_id == document.document_id,
                        versions.c.version == document.version,
                    )
                )
            ).scalar_one()
            if stored != digest:
                raise Conflict("Document content changed without a new version")
            await self._ignore(
                conn,
                heads,
                dict(
                    tenant_id=document.tenant_id,
                    document_id=document.document_id,
                    generation_id=None,
                    revision=0,
                    version=0,
                    deleted=False,
                    access_group=None,
                ),
                ["tenant_id", "document_id"],
            )
            await self._ignore(
                conn,
                generations,
                dict(
                    id=claim["id"],
                    tenant_id=document.tenant_id,
                    document_id=document.document_id,
                    version=document.version,
                    embedding_id=embedding_id,
                    expected_revision=expected_revision,
                    status="staging",
                    source_metadata=redact(document.model_dump(mode="json", exclude={"text"})),
                    document_hash=digest,
                ),
                ["id"],
            )
            generation = (
                (await conn.execute(select(generations).where(generations.c.id == claim["id"])))
                .mappings()
                .one()
            )
            if (
                generation["embedding_id"] != embedding_id
                or generation["expected_revision"] != expected_revision
                or generation["source_metadata"]
                != redact(document.model_dump(mode="json", exclude={"text"}))
                or generation["document_hash"] != digest
            ):
                raise Conflict("Generation configuration changed during replay")

    async def save_batch(self, claim, ordinal, source_chunks: list[Chunk], vectors):
        if not source_chunks or len(source_chunks) != len(vectors):
            raise ValueError("Invalid embedding batch count")
        dimensions = len(vectors[0])
        if dimensions == 0 or any(
            len(v) != dimensions or not any(v) or not all(math.isfinite(value) for value in v)
            for v in vectors
        ):
            raise ValueError("Invalid embedding vectors")
        checksum = fingerprint([[c.model_dump(mode="json") for c in source_chunks], vectors])
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            generation = (
                (await conn.execute(select(generations).where(generations.c.id == claim["id"])))
                .mappings()
                .one()
            )
            for chunk in source_chunks:
                if (
                    chunk.tenant_id != claim["tenant_id"]
                    or chunk.document_id != generation["document_id"]
                    or chunk.version != generation["version"]
                    or chunk.document_hash != generation["document_hash"]
                    or hashlib.sha256(chunk.text.encode()).hexdigest() != chunk.content_hash
                    or any(
                        chunk.model_dump(mode="json")[k] != v
                        for k, v in generation["source_metadata"].items()
                    )
                ):
                    raise Conflict("Chunk provenance does not match generation")
            previous = (
                await conn.execute(
                    select(batches.c.checksum).where(
                        batches.c.generation_id == claim["id"], batches.c.ordinal == ordinal
                    )
                )
            ).scalar_one_or_none()
            if previous:
                if previous != checksum:
                    raise Conflict("Embedding batch changed during replay")
                return
            for chunk, vector in zip(source_chunks, vectors, strict=True):
                await self._ignore(
                    conn,
                    embedding_cache,
                    dict(
                        tenant_id=claim["tenant_id"],
                        embedding_id=generation["embedding_id"],
                        content_hash=chunk.content_hash,
                        embedding=vector,
                    ),
                    ["tenant_id", "embedding_id", "content_hash"],
                )
                await conn.execute(
                    insert(chunks).values(
                        generation_id=claim["id"],
                        chunk_id=chunk.chunk_id,
                        tenant_id=chunk.tenant_id,
                        document_id=chunk.document_id,
                        access_group=chunk.access_group,
                        service=chunk.service,
                        region=chunk.region,
                        release=chunk.release,
                        source_status=chunk.status,
                        valid_from=chunk.effective_from.isoformat(),
                        valid_to=chunk.effective_to.isoformat() if chunk.effective_to else None,
                        payload=chunk.model_dump(mode="json"),
                        embedding=vector,
                    )
                )
            await conn.execute(
                insert(batches).values(
                    generation_id=claim["id"],
                    ordinal=ordinal,
                    checksum=checksum,
                    chunk_ids=[c.chunk_id for c in source_chunks],
                )
            )
            await self._event(
                conn,
                claim["id"],
                "batch_checkpoint",
                {"batch": ordinal, "chunks": len(source_chunks)},
            )

    async def cached_vectors(self, actor, embedding_id, pieces):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(embedding_cache).where(
                            embedding_cache.c.tenant_id == actor.tenant_id,
                            embedding_cache.c.embedding_id == embedding_id,
                            embedding_cache.c.content_hash.in_(
                                [piece.content_hash for piece in pieces]
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            return {row["content_hash"]: row["embedding"] for row in rows}

    async def generation_batches(self, claim):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(batches)
                        .where(batches.c.generation_id == claim["id"])
                        .order_by(batches.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            result = {}
            for row in rows:
                records = (
                    (
                        await conn.execute(
                            select(chunks).where(
                                chunks.c.generation_id == claim["id"],
                                chunks.c.chunk_id.in_(row["chunk_ids"]),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                by_id = {item["chunk_id"]: item for item in records}
                result[row["ordinal"]] = [
                    (Chunk.model_validate(by_id[c]["payload"]), by_id[c]["embedding"])
                    for c in row["chunk_ids"]
                ]
            return result

    async def promote(self, claim, *, expected_chunks):
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            generation = (
                (await conn.execute(select(generations).where(generations.c.id == claim["id"])))
                .mappings()
                .one()
            )
            await self._write_access(conn, claim, generation["document_id"])
            count = (
                await conn.execute(
                    select(func.count())
                    .select_from(chunks)
                    .where(chunks.c.generation_id == claim["id"])
                )
            ).scalar_one()
            if count != expected_chunks or count == 0:
                raise Conflict("Generation is incomplete")
            switched = await conn.execute(
                update(heads)
                .where(
                    heads.c.tenant_id == claim["tenant_id"],
                    heads.c.document_id == generation["document_id"],
                    heads.c.revision == generation["expected_revision"],
                    heads.c.version <= generation["version"],
                )
                .values(
                    generation_id=claim["id"],
                    revision=heads.c.revision + 1,
                    version=generation["version"],
                    deleted=False,
                    access_group=generation["source_metadata"]["access_group"],
                )
            )
            if switched.rowcount != 1:
                raise Conflict("Document revision changed before publication")
            await conn.execute(
                update(generations).where(generations.c.id == claim["id"]).values(status="active")
            )
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == claim["id"])
                .values(
                    status="succeeded",
                    result={"generation_id": claim["id"], "chunks": count},
                    lease_until=0,
                )
            )
            await self._event(conn, claim["id"], "succeeded", {"chunks": count})

    async def retire(self, claim, document_id, expected_revision):
        async with self.engine.begin() as conn:
            await self._owned(conn, claim)
            await self._write_access(conn, claim, document_id)
            result = await conn.execute(
                update(heads)
                .where(
                    heads.c.tenant_id == claim["tenant_id"],
                    heads.c.document_id == document_id,
                    heads.c.revision == expected_revision,
                )
                .values(generation_id=None, deleted=True, revision=heads.c.revision + 1)
            )
            if result.rowcount != 1:
                raise Conflict("Document revision changed")
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == claim["id"])
                .values(status="succeeded", result={"retired": document_id}, lease_until=0)
            )
            await self._event(conn, claim["id"], "succeeded")

    async def _write_access(self, conn, claim, document_id):
        group = (
            await conn.execute(
                select(heads.c.access_group).where(
                    heads.c.tenant_id == claim["tenant_id"], heads.c.document_id == document_id
                )
            )
        ).scalar_one_or_none()
        if group is not None and group not in claim["groups"]:
            raise Conflict("Current document access denies this write")

    def _authorized(self, actor, question=None, embedding_id=None):
        today = datetime.now(UTC).date().isoformat()
        conditions = [
            chunks.c.tenant_id == actor.tenant_id,
            heads.c.tenant_id == actor.tenant_id,
            heads.c.deleted.is_(False),
            chunks.c.access_group.in_(actor.groups),
            chunks.c.source_status == "active",
            chunks.c.valid_from <= today,
            or_(chunks.c.valid_to.is_(None), chunks.c.valid_to > today),
        ]
        if question:
            conditions += [
                chunks.c.service == question.service,
                chunks.c.region == question.region,
                chunks.c.release == question.release,
            ]
        statement = select(chunks).join(
            heads,
            and_(
                heads.c.tenant_id == chunks.c.tenant_id,
                heads.c.document_id == chunks.c.document_id,
                heads.c.generation_id == chunks.c.generation_id,
            ),
        )
        if embedding_id:
            statement = statement.join(generations, generations.c.id == chunks.c.generation_id)
            conditions.append(generations.c.embedding_id == embedding_id)
        return statement.where(*conditions).order_by(chunks.c.document_id, chunks.c.chunk_id)

    async def authorized_records(
        self, actor, question: Question | None = None, *, limit=2000, embedding_id=None
    ):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        self._authorized(actor, question, embedding_id).limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            if len(rows) > limit:
                raise ValueError("Authorized corpus exceeds local hybrid retrieval limit")
            return [dict(row) for row in rows]

    async def authorized_chunks(self, actor, question=None, *, limit=2000):
        return [
            Chunk.model_validate(r["payload"])
            for r in await self.authorized_records(actor, question, limit=limit)
        ]

    async def get_chunk(self, actor, chunk_id, *, embedding_id=None):
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        self._authorized(actor, embedding_id=embedding_id).where(
                            chunks.c.chunk_id == chunk_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            return Chunk.model_validate(row["payload"]) if row else None
