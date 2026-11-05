import asyncio
import json
import random
import time
from contextlib import AsyncExitStack
from dataclasses import replace
from uuid import uuid4

import httpx
from langsmith import tracing_context
from sqlalchemy.exc import OperationalError

from incidentops.chunking import RecursiveDocumentChunker
from incidentops.durable.config import WorkflowSettings
from incidentops.durable.errors import retryable_dependency_error
from incidentops.durable.local_index import LocalVectorIndex
from incidentops.durable.repository import Conflict, LostLease, Repository
from incidentops.models import (
    Assessment,
    Chunk,
    Citation,
    Claim,
    Draft,
    Metrics,
    Principal,
    Question,
    SourceDocument,
)
from incidentops.object_storage import LocalObjectStore, ObjectRef, S3ObjectStore
from incidentops.providers import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    FakeGenerator,
    OpenAIGenerator,
    make_embeddings,
    render_context,
)
from incidentops.retrieval.hybrid import HybridRetriever, RetrievalPolicy
from incidentops.retrieval.indexes import PgVectorIndex, PineconeVectorIndex
from incidentops.security import redact, token_upper_bound
from incidentops.tracing import TraceSink


class UnclaimedInfrastructureError(ConnectionError):
    """Transport retry is safe: the worker did not acquire a job lease."""


class Runtime:
    def __init__(
        self,
        settings,
        repo,
        objects,
        index,
        embeddings,
        generator,
        embedding_id,
        *,
        http_client=None,
        async_http_client=None,
    ):
        self.settings, self.repo, self.objects, self.index = settings, repo, objects, index
        self.embeddings, self.generator, self.embedding_id = embeddings, generator, embedding_id
        self.http_client, self.async_http_client = http_client, async_http_client
        self.chunker = RecursiveDocumentChunker(
            settings.chunk_size_chars, settings.chunk_overlap_chars
        )
        self.hybrid = HybridRetriever(
            index,
            RetrievalPolicy(
                dense_k=settings.dense_k,
                lexical_k=settings.lexical_k,
                rerank_k=settings.rerank_k,
                final_k=settings.final_k,
                rrf_constant=settings.rrf_constant,
                max_corpus_chunks=settings.retrieval_max_chunks,
            ),
        )
        self.provider_slots = asyncio.Semaphore(settings.provider_concurrency)
        self.trace_sink = TraceSink(
            settings.tracing,
            settings.trace_project,
            api_key=settings.langsmith_api_key.get_secret_value()
            if settings.langsmith_api_key
            else None,
        )

    def ingestion_profile(self):
        return {
            "splitter": self.chunker.profile,
            "batch_size": self.settings.embedding_batch_size,
            "embedding_id": self.embedding_id,
        }

    @staticmethod
    def citation(chunk):
        return Citation.from_chunk(chunk).model_copy(
            update={"source_url": f"/v2/sources/{chunk.chunk_id}"}
        )

    async def submit_document(self, actor, key, document: SourceDocument, expected_revision=None):
        if document.tenant_id != actor.tenant_id or document.access_group not in actor.groups:
            raise PermissionError("Document is outside principal scope")
        safe = SourceDocument.model_validate(redact(document.model_dump(mode="json")))
        ref = await self.objects.put(actor.tenant_id, safe.model_dump_json().encode())
        revision = (
            await self.repo.head_revision(actor, safe.document_id)
            if expected_revision is None
            else expected_revision
        )
        payload = {
            "object": ref.model_dump(),
            "expected_revision": revision,
            "profile": self.ingestion_profile(),
            "queue": "ingestion.large" if len(safe.text.encode()) > 20000 else "ingestion.small",
        }
        return await self.repo.submit(
            actor,
            "ingest",
            key,
            payload,
            deadline_seconds=self.settings.job_deadline_seconds,
            request_identity={
                "document": safe.model_dump(mode="json"),
                "expected_revision": expected_revision,
            },
        )

    async def submit_question(self, actor, key, question: Question):
        payload = {
            "question": redact(question.model_dump()),
            "embedding_id": self.embedding_id,
            "queue": "investigations",
        }
        return await self.repo.submit(
            actor, "answer", key, payload, deadline_seconds=self.settings.job_deadline_seconds
        )

    async def submit_delete(self, actor, key, document_id, expected_revision):
        return await self.repo.submit(
            actor,
            "delete",
            key,
            {
                "document_id": document_id,
                "expected_revision": expected_revision,
                "queue": "ingestion.small",
            },
            deadline_seconds=self.settings.job_deadline_seconds,
        )

    async def job_view(self, actor, job_id):
        job = await self.repo.get_job(actor, job_id)
        if job is None:
            return None
        result = job["result"]
        if result and "assessment" in result:
            assessment = Assessment.model_validate(result["assessment"])
            if not await self._valid_result(actor, assessment):
                result = {
                    "assessment": assessment.model_copy(
                        update={
                            "status": "insufficient_evidence",
                            "reason": "invalid_grounding",
                            "claims": [],
                            "retrieved": [],
                        }
                    ).model_dump(),
                    "invalidated": True,
                }
            elif "retrieval" in result:
                result["retrieval"]["duplicate_sources"] = await self._visible_duplicates(
                    actor, result["retrieval"].get("duplicate_sources", {})
                )
        return {
            key: job[key]
            for key in [
                "id",
                "kind",
                "status",
                "attempt",
                "created_at",
                "deadline",
                "error_code",
                "event_sequence",
            ]
        } | {"result": result}

    async def _visible_duplicates(self, actor, duplicate_sources):
        visible = {}
        for representative, identifiers in duplicate_sources.items():
            allowed = []
            for identifier in identifiers:
                if (
                    await self.repo.get_chunk(actor, identifier, embedding_id=self.embedding_id)
                    is not None
                ):
                    allowed.append(identifier)
            if representative in allowed:
                visible[representative] = allowed
        return visible

    async def _valid_result(self, actor, assessment):
        for citation in [*assessment.retrieved, *(c.citation for c in assessment.claims)]:
            current = await self.repo.get_chunk(
                actor, citation.chunk_id, embedding_id=self.embedding_id
            )
            if current is None or self.citation(current) != citation:
                return False
        for claim in assessment.claims:
            source = await self.repo.get_chunk(
                actor, claim.citation.chunk_id, embedding_id=self.embedding_id
            )
            if source is None or claim.quote not in source.text:
                return False
        return True

    async def retrieve(self, actor, question):
        safe_question = question.model_copy(update={"question": redact(question.question)})
        records = await self.repo.authorized_records(
            actor,
            safe_question,
            limit=self.settings.retrieval_max_chunks,
            embedding_id=self.embedding_id,
        )
        if not records:
            return None
        with tracing_context(enabled=False):
            async with self.provider_slots:
                vector = await self.embeddings.aembed_query(safe_question.question)
        result = await self.hybrid.retrieve(
            actor.tenant_id,
            safe_question.question,
            vector,
            sorted({r["generation_id"] for r in records}),
            [Chunk.model_validate(r["payload"]) for r in records],
        )
        # Reauthorization also covers evidence returned by retrieval-only endpoints.
        for item in result.evidence:
            if (
                await self.repo.get_chunk(
                    actor, item.chunk.chunk_id, embedding_id=self.embedding_id
                )
                != item.chunk
            ):
                raise Conflict("Evidence changed during retrieval")
        return replace(
            result,
            duplicate_sources=await self._visible_duplicates(actor, result.duplicate_sources),
        )

    async def _answer(self, actor, question):
        started = time.perf_counter()
        result = await self.retrieve(actor, question)
        evidence = []
        if result:
            for candidate in result.evidence:
                if (
                    token_upper_bound(render_context([*evidence, candidate]))
                    <= self.settings.context_tokens
                ):
                    evidence.append(candidate)
        claims, reason = [], "no_evidence"
        if evidence:
            bound = (
                sum(
                    token_upper_bound(s)
                    for s in [
                        SYSTEM_PROMPT,
                        question.question,
                        render_context(evidence),
                        json.dumps(Draft.model_json_schema()),
                    ]
                )
                + 256
            )
            if bound > self.settings.max_input_token_bound:
                raise ValueError("Input token upper bound exceeds job budget")
            if self.generator is None:
                self.generator = OpenAIGenerator(
                    self.settings.model_copy(update={"max_retries": 0}),
                    http_client=self.http_client,
                    http_async_client=self.async_http_client,
                )
            with tracing_context(enabled=False):
                async with self.provider_slots:
                    draft = Draft.model_validate(
                        await self.generator.agenerate(redact(question.question), evidence)
                    )
            reason = "not_answerable"
            if draft.answerable and draft.claims:
                by_id = {e.chunk.chunk_id: e.chunk for e in evidence}
                for proposal in draft.claims:
                    chunk = by_id.get(proposal.evidence_id)
                    current = await self.repo.get_chunk(
                        actor, proposal.evidence_id, embedding_id=self.embedding_id
                    )
                    if (
                        chunk is None
                        or current != chunk
                        or proposal.quote not in chunk.text
                        or proposal.quote != redact(proposal.quote)
                    ):
                        reason, claims = "invalid_grounding", []
                        break
                    claims.append(Claim(quote=proposal.quote, citation=self.citation(chunk)))
                else:
                    reason = "verified_quotes"
        assessment = Assessment(
            run_id=str(uuid4()),
            mode=self.settings.provider,
            status="answered" if claims else "insufficient_evidence",
            reason=reason,
            claims=claims,
            retrieved=[self.citation(e.chunk) for e in evidence],
            metrics=Metrics(
                latency_ms=(time.perf_counter() - started) * 1000,
                context_tokens=token_upper_bound(render_context(evidence)) if evidence else 0,
                retrieved_chunks=len(evidence),
                prompt_version=PROMPT_VERSION,
            ),
        )
        if not await self._valid_result(actor, assessment):
            assessment = assessment.model_copy(
                update={
                    "status": "insufficient_evidence",
                    "reason": "invalid_grounding",
                    "claims": [],
                    "retrieved": [],
                }
            )
        if self.trace_sink.enabled:
            await asyncio.to_thread(
                self.trace_sink.emit,
                assessment,
                [("background_answer", assessment.metrics.latency_ms)],
            )
        return {
            "assessment": assessment.model_dump(),
            "retrieval": {
                "mode": "bm25+dense-rrf",
                "dense_candidates": len(result.dense_ids) if result else 0,
                "lexical_candidates": len(result.lexical_ids) if result else 0,
                "duplicate_sources": result.duplicate_sources if result else {},
                "reranker": "disabled",
                "billed_tokens": None,
                "cost_usd": None,
            },
        }

    async def _ingest(self, claim, actor):
        payload = claim["payload"]
        if payload["profile"] != self.ingestion_profile():
            raise Conflict("Ingestion profile changed; submit a new job")
        raw = await self.objects.get(actor.tenant_id, ObjectRef.model_validate(payload["object"]))
        document = SourceDocument.model_validate_json(raw)
        if document.tenant_id != actor.tenant_id or document.access_group not in actor.groups:
            raise PermissionError("Stored source is outside principal scope")
        pieces = await asyncio.to_thread(self.chunker.split, document)
        if not pieces or len(pieces) > self.settings.ingestion_max_chunks:
            raise ValueError("Ingestion chunk count exceeds budget")
        await self.repo.begin_generation(
            claim, document, self.embedding_id, expected_revision=payload["expected_revision"]
        )
        completed = await self.repo.generation_batches(claim)
        batch_size = self.settings.embedding_batch_size
        for ordinal, offset in enumerate(range(0, len(pieces), batch_size)):
            batch = pieces[offset : offset + batch_size]
            if ordinal in completed:
                prior = completed[ordinal]
                if [c for c, _ in prior] != batch:
                    raise Conflict("Chunk manifest changed during replay")
                vectors = [v for _, v in prior]
            else:
                cached = await self.repo.cached_vectors(actor, self.embedding_id, batch)
                missing = {c.content_hash: c.text for c in batch if c.content_hash not in cached}
                if missing:
                    async with self.provider_slots:
                        with tracing_context(enabled=False):
                            fresh = await self.embeddings.aembed_documents(list(missing.values()))
                    if len(fresh) != len(missing):
                        raise ValueError("Embedding batch count mismatch")
                    cached.update(zip(missing, fresh, strict=True))
                vectors = [cached[c.content_hash] for c in batch]
                await self.repo.save_batch(claim, ordinal, batch, vectors)
            # Safe to repeat if crash happens between vector write and head promotion.
            await self.index.upsert(actor.tenant_id, claim["id"], batch, vectors)
        await self.repo.promote(claim, expected_chunks=len(pieces))

    async def run_job(self, job_id):
        try:
            claim = await self.repo.claim(
                job_id,
                lease_seconds=self.settings.lease_seconds,
                max_attempts=self.settings.worker_max_attempts,
            )
        except (OperationalError, ConnectionError, OSError) as exc:
            state = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if isinstance(exc, PermissionError) or state in {"28000", "28P01", "42501"}:
                raise
            raise UnclaimedInfrastructureError("Job persistence unavailable before claim") from exc
        if claim is None:
            return False
        actor = Principal(claim["tenant_id"], claim["principal_id"], tuple(claim["groups"]))

        async def heartbeat():
            while True:
                await asyncio.sleep(self.settings.lease_seconds / 3)
                await self.repo.heartbeat(claim, self.settings.lease_seconds)

        pulse = asyncio.create_task(heartbeat())

        async def execute():
            if claim["kind"] == "ingest":
                await self._ingest(claim, actor)
            elif claim["kind"] == "delete":
                await self.repo.retire(
                    claim, claim["payload"]["document_id"], claim["payload"]["expected_revision"]
                )
            else:
                if claim["payload"]["embedding_id"] != self.embedding_id:
                    raise Conflict("Embedding configuration changed")
                result = await self._answer(
                    actor, Question.model_validate(claim["payload"]["question"])
                )
                await self.repo.complete(claim, result)

        work = asyncio.create_task(execute())
        try:
            async with asyncio.timeout(max(0.001, claim["deadline"] - self.repo.clock())):
                done, _ = await asyncio.wait({pulse, work}, return_when=asyncio.FIRST_COMPLETED)
                if work in done:
                    await work
                else:
                    await pulse  # Ownership/heartbeat failure cancels long outstanding work.
        except LostLease:
            return False
        except Exception as exc:
            transient = retryable_dependency_error(exc)
            code = (
                "dependency_transient"
                if transient
                else "conflict"
                if isinstance(exc, Conflict)
                else "invalid_work"
            )
            try:
                await self.repo.retry_or_fail(
                    claim,
                    code,
                    transient=transient,
                    delay=random.uniform(0.5, min(30, 2 ** claim["attempt"])),
                    max_attempts=self.settings.worker_max_attempts,
                )
            except LostLease:
                await self.repo.recover_expired(self.settings.worker_max_attempts)
            return False
        finally:
            for task in (pulse, work):
                task.cancel()
            await asyncio.gather(pulse, work, return_exceptions=True)
        return True

    async def aclose(self):
        closing = [self.index.aclose(), self.objects.aclose(), self.repo.aclose()]
        if self.async_http_client:
            closing.append(self.async_http_client.aclose())
        if self.http_client:
            closing.append(asyncio.to_thread(self.http_client.close))
        if self.trace_sink.client:

            def close_trace():
                for method in ("flush", "close"):
                    operation = getattr(self.trace_sink.client, method, None)
                    if operation:
                        operation()

            closing.append(asyncio.to_thread(close_trace))
        results = await asyncio.gather(*closing, return_exceptions=True)
        if any(isinstance(result, Exception) for result in results):
            raise RuntimeError("One or more runtime resources failed to close")


async def create_runtime(settings: WorkflowSettings):
    if settings.tracing and not settings.langsmith_api_key:
        raise ValueError("LangSmith key is required when workflow tracing is enabled")
    if settings.workflow_database_url.startswith("sqlite"):
        from pathlib import Path

        Path(settings.workflow_database_url.split("///", 1)[-1]).parent.mkdir(
            parents=True, exist_ok=True
        )
    # Until Runtime owns every adapter, a later constructor can fail. Register
    # cleanup immediately after each successful allocation, including owned HTTP
    # clients supplied to the provider SDKs, and transfer ownership only at end.
    async with AsyncExitStack() as cleanup:
        repo = Repository(settings.workflow_database_url)
        cleanup.push_async_callback(repo.aclose)
        # The workflow owns retries; avoid multiplying SDK retries by job retries.
        provider_settings = settings.model_copy(update={"max_retries": 0})
        sync_http = async_http = None
        if settings.provider != "fake":
            sync_http = httpx.Client(timeout=settings.timeout_seconds)
            cleanup.push_async_callback(asyncio.to_thread, sync_http.close)
            async_http = httpx.AsyncClient(
                timeout=settings.timeout_seconds,
                limits=httpx.Limits(max_connections=settings.provider_concurrency),
            )
            cleanup.push_async_callback(async_http.aclose)
        embeddings, identity, dimensions = make_embeddings(
            provider_settings, http_client=sync_http, http_async_client=async_http
        )
        generator = FakeGenerator() if settings.provider == "fake" else None
        objects = (
            LocalObjectStore(settings.object_root)
            if settings.object_backend == "local"
            else S3ObjectStore(settings.s3_bucket)
        )
        cleanup.push_async_callback(objects.aclose)
        if settings.vector_backend == "local":
            index = LocalVectorIndex(repo, dimensions)
        elif settings.vector_backend == "pgvector":
            index = PgVectorIndex(
                settings.vector_database_url,
                dimensions=dimensions,
                embedding_identity=identity,
                hnsw=settings.hnsw,
                ef_search=settings.hnsw_ef_search,
                exact=settings.vector_exact,
            )
        else:
            index = PineconeVectorIndex.from_host(
                api_key=settings.pinecone_api_key.get_secret_value(),
                host=settings.pinecone_host,
                dimensions=dimensions,
                embedding_identity=identity,
            )
        cleanup.push_async_callback(index.aclose)
        result = Runtime(
            settings,
            repo,
            objects,
            index,
            embeddings,
            generator,
            identity,
            http_client=sync_http,
            async_http_client=async_http,
        )
        cleanup.pop_all()
        return result
