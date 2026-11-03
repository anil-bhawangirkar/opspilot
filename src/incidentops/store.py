"""Explicit SQL keeps authorization visible before vector ranking.

PostgreSQL uses pgvector exact cosine search. SQLite is a development adapter,
not a substitute for exercising PostgreSQL's query semantics in integration tests.
"""

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from incidentops.models import Chunk, Evidence, Principal, Question


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / denominator if denominator else 0.0


class Store:
    def __init__(self, url: str, embedding_id: str, dimensions: int):
        self.url, self.embedding_id, self.dimensions = url, embedding_id, dimensions
        self.postgres = url.startswith("postgresql://")
        if not self.postgres and not url.startswith("sqlite:///"):
            raise ValueError("Use a postgresql:// or sqlite:/// database URL")
        if not self.postgres:
            self.path = Path(url.removeprefix("sqlite:///"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = (
            psycopg.connect(self.url, connect_timeout=5, options="-c statement_timeout=5000")
            if self.postgres
            else sqlite3.connect(self.path, timeout=5)
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _execute(self, connection, sql, params=()):
        return connection.execute(sql if self.postgres else sql.replace("%s", "?"), params)

    def _initialize(self):
        with self._connection() as conn:
            if self.postgres:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._execute(
                conn,
                "CREATE TABLE IF NOT EXISTS index_config "
                "(singleton INTEGER PRIMARY KEY, embedding_id TEXT, dimensions INTEGER)",
            )
            self._execute(
                conn,
                "INSERT INTO index_config VALUES (1, %s, %s) ON CONFLICT (singleton) DO NOTHING",
                (self.embedding_id, self.dimensions),
            )
            config = self._execute(
                conn, "SELECT embedding_id, dimensions FROM index_config"
            ).fetchone()
            if config != (self.embedding_id, self.dimensions):
                raise ValueError("Embedding configuration changed; use a new database and reingest")
            vector_type = f"vector({int(self.dimensions)})" if self.postgres else "TEXT"
            self._execute(
                conn,
                f"""CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
                version INTEGER NOT NULL, access_group TEXT NOT NULL, service TEXT NOT NULL,
                region TEXT NOT NULL, release TEXT NOT NULL, status TEXT NOT NULL,
                valid_from TEXT NOT NULL, valid_to TEXT, payload TEXT NOT NULL,
                embedding {vector_type} NOT NULL)""",
            )
            self._execute(
                conn,
                "CREATE INDEX IF NOT EXISTS chunks_scope "
                "ON chunks (tenant_id, access_group, service, region, release)",
            )
            self._execute(
                conn,
                "CREATE TABLE IF NOT EXISTS document_versions "
                "(tenant_id TEXT, document_id TEXT, version INTEGER, document_hash TEXT, "
                "PRIMARY KEY (tenant_id, document_id, version))",
            )

    def _validate_vector(self, vector):
        if len(vector) != self.dimensions or not all(math.isfinite(v) for v in vector):
            raise ValueError("Invalid embedding vector dimension or non-finite value")
        if not any(vector):
            raise ValueError("Cosine search requires a non-zero vector")

    def snapshot_fingerprint(self) -> str:
        with self._connection() as conn:
            rows = self._execute(conn, "SELECT payload FROM chunks ORDER BY chunk_id").fetchall()
        return hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()

    def replace_corpus(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Embedding count must equal chunk count")
        for vector in vectors:
            self._validate_vector(vector)
        with self._connection() as conn:
            # Single ingestion writer in increment 1; lock serializes PG replacement.
            if self.postgres:
                conn.execute("LOCK TABLE document_versions IN EXCLUSIVE MODE")
            versions = {(c.tenant_id, c.document_id, c.version): c.document_hash for c in chunks}
            for key, digest in versions.items():
                self._execute(
                    conn,
                    "INSERT INTO document_versions VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (tenant_id, document_id, version) DO NOTHING",
                    (*key, digest),
                )
                old = self._execute(
                    conn,
                    "SELECT document_hash FROM document_versions WHERE "
                    "tenant_id=%s AND document_id=%s AND version=%s",
                    key,
                ).fetchone()
                if old[0] != digest:
                    raise ValueError("Source text changed without a new document version")
            self._execute(conn, "DELETE FROM chunks")
            vector_slot = "%s::vector" if self.postgres else "%s"
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._execute(
                    conn,
                    "INSERT INTO chunks VALUES "
                    f"(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{vector_slot})",
                    (
                        chunk.chunk_id,
                        chunk.tenant_id,
                        chunk.document_id,
                        chunk.version,
                        chunk.access_group,
                        chunk.service,
                        chunk.region,
                        chunk.release,
                        chunk.status,
                        chunk.effective_from.isoformat(),
                        chunk.effective_to.isoformat() if chunk.effective_to else None,
                        chunk.model_dump_json(),
                        json.dumps(vector),
                    ),
                )

    def _scope(self, principal: Principal, as_of: date):
        slots = ",".join("%s" for _ in principal.groups) or "NULL"
        return (
            f"tenant_id=%s AND access_group IN ({slots}) AND status='active' "
            "AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)",
            [principal.tenant_id, *principal.groups, as_of.isoformat(), as_of.isoformat()],
        )

    def search(
        self,
        principal: Principal,
        question: Question,
        vector: list[float],
        limit: int,
        *,
        as_of: date | None = None,
    ) -> list[Evidence]:
        self._validate_vector(vector)
        where, params = self._scope(principal, as_of or datetime.now(UTC).date())
        where += " AND service=%s AND region=%s AND release=%s"
        params += [question.service, question.region, question.release]
        with self._connection() as conn:
            if self.postgres:
                rows = self._execute(
                    conn,
                    "SELECT payload, 1 - (embedding <=> %s::vector) "
                    f"FROM chunks WHERE {where} "
                    "ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s",
                    [json.dumps(vector), *params, json.dumps(vector), limit],
                ).fetchall()
                return [
                    Evidence(chunk=Chunk.model_validate_json(p), score=float(s)) for p, s in rows
                ]
            rows = self._execute(
                conn, f"SELECT payload, embedding FROM chunks WHERE {where}", params
            ).fetchall()
        found = [
            Evidence(chunk=Chunk.model_validate_json(p), score=cosine(vector, json.loads(v)))
            for p, v in rows
        ]
        return sorted(found, key=lambda e: (-e.score, e.chunk.chunk_id))[:limit]

    def get_chunk(self, principal: Principal, chunk_id: str) -> Chunk | None:
        where, params = self._scope(principal, datetime.now(UTC).date())
        with self._connection() as conn:
            row = self._execute(
                conn,
                f"SELECT payload FROM chunks WHERE {where} AND chunk_id=%s",
                [*params, chunk_id],
            ).fetchone()
        return Chunk.model_validate_json(row[0]) if row else None
