"""Vector adapters return IDs, never authoritative source/ACL metadata.

Callers must hydrate against their current authorized generation snapshot. Remote
index acknowledgement does not prove immediate query visibility (notably on an
eventually-consistent service). Generation publication belongs to the job service.
"""

import hashlib
import json
import math
import re
from contextlib import asynccontextmanager

import psycopg

from incidentops.retrieval.contracts import VectorHit


def _scope(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("Invalid tenant or generation identifier")


def _vector(value: list[float], dimensions: int) -> None:
    if len(value) != dimensions or not all(math.isfinite(v) for v in value) or not any(value):
        raise ValueError("Expected a finite non-zero vector with configured dimensions")


def _write_inputs(tenant_id, generation_id, chunks, vectors, dimensions):
    _scope(tenant_id)
    _scope(generation_id)
    if len(chunks) != len(vectors):
        raise ValueError("Vector count must equal chunk count")
    if len(chunks) > 1000:
        raise ValueError("Upsert batch exceeds adapter limit")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("Duplicate chunk IDs in batch")
    if any(chunk.tenant_id != tenant_id for chunk in chunks):
        raise ValueError("Cross-tenant vector write rejected")
    for vector in vectors:
        _vector(vector, dimensions)


def _search_inputs(tenant_id, vector, generation_ids, limit, dimensions):
    _scope(tenant_id)
    _vector(vector, dimensions)
    if not 1 <= limit <= 1000 or len(generation_ids) > 2000:
        raise ValueError("Search budget exceeds adapter bounds")
    for generation_id in generation_ids:
        _scope(generation_id)


def _configuration(dimensions: int, embedding_identity: str):
    if type(dimensions) is not int or not 1 <= dimensions <= 2000 or not embedding_identity:
        raise ValueError("Invalid vector index configuration")


class MemoryVectorIndex:
    """Explicitly ephemeral exact-search adapter for tests and local demonstrations."""

    def __init__(self, dimensions: int, embedding_identity: str):
        _configuration(dimensions, embedding_identity)
        self.dimensions, self.embedding_identity = dimensions, embedding_identity
        self._rows = {}

    async def upsert(self, tenant_id, generation_id, chunks, vectors):
        _write_inputs(tenant_id, generation_id, chunks, vectors, self.dimensions)
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._rows[(tenant_id, generation_id, chunk.chunk_id)] = list(vector)

    async def search(self, tenant_id, vector, generation_ids, limit):
        _search_inputs(tenant_id, vector, generation_ids, limit, self.dimensions)
        generations = set(generation_ids)
        scores = {}
        query_norm = math.sqrt(sum(v * v for v in vector))
        for (tenant, generation, chunk_id), embedding in self._rows.items():
            if tenant == tenant_id and generation in generations:
                score = sum(a * b for a, b in zip(vector, embedding, strict=True)) / (
                    query_norm * math.sqrt(sum(v * v for v in embedding))
                )
                scores[chunk_id] = max(scores.get(chunk_id, -math.inf), score)
        return [
            VectorHit(key, score)
            for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    async def delete_generation(self, tenant_id, generation_id):
        _scope(tenant_id)
        _scope(generation_id)
        self._rows = {
            key: value for key, value in self._rows.items() if key[:2] != (tenant_id, generation_id)
        }

    async def aclose(self):
        pass


class PineconeVectorIndex:
    """Native async Pinecone data-plane adapter; index creation is an operator action.

    Supply an initialized IndexAsyncio. Raw source text and credentials are never
    written into vector metadata. The configured remote index must match the
    supplied dimensions/model; validate that at deployment, not by paid test calls.
    """

    def __init__(self, index, *, dimensions: int, embedding_identity: str):
        _configuration(dimensions, embedding_identity)
        self.index = index
        self.dimensions, self.embedding_identity = dimensions, embedding_identity

    @classmethod
    def from_host(cls, *, api_key: str, host: str, dimensions: int, embedding_identity: str):
        from pinecone import Pinecone

        # Constructing the host-targeted data-plane client makes no control-plane call.
        client = Pinecone(api_key=api_key)
        index = client.IndexAsyncio(host=host)
        client.close()
        return cls(index, dimensions=dimensions, embedding_identity=embedding_identity)

    def _filter(self, generation_clause):
        return {
            "$and": [generation_clause, {"embedding_identity": {"$eq": self.embedding_identity}}]
        }

    async def upsert(self, tenant_id, generation_id, chunks, vectors):
        _write_inputs(tenant_id, generation_id, chunks, vectors, self.dimensions)
        records = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            identity = json.dumps([tenant_id, generation_id, chunk.chunk_id])
            records.append(
                {
                    "id": hashlib.sha256(identity.encode()).hexdigest(),
                    "values": vector,
                    "metadata": {
                        "chunk_id": chunk.chunk_id,
                        "generation_id": generation_id,
                        "embedding_identity": self.embedding_identity,
                    },
                }
            )
        if records:
            await self.index.upsert(vectors=records, namespace=tenant_id)

    async def search(self, tenant_id, vector, generation_ids, limit):
        _search_inputs(tenant_id, vector, generation_ids, limit, self.dimensions)
        if not generation_ids:
            return []
        result = await self.index.query(
            namespace=tenant_id,
            vector=vector,
            top_k=limit,
            include_metadata=True,
            include_values=False,
            filter=self._filter({"generation_id": {"$in": sorted(set(generation_ids))}}),
        )
        matches = result.get("matches", []) if isinstance(result, dict) else result.matches
        found = {}
        for match in matches:
            metadata = match.get("metadata") if isinstance(match, dict) else match.metadata
            score = match.get("score") if isinstance(match, dict) else match.score
            if (
                not metadata
                or metadata.get("generation_id") not in generation_ids
                or metadata.get("embedding_identity") != self.embedding_identity
            ):
                continue
            identifier = metadata.get("chunk_id")
            if (
                isinstance(identifier, str)
                and isinstance(score, (int, float))
                and math.isfinite(score)
            ):
                found[identifier] = max(found.get(identifier, -math.inf), score)
        return [
            VectorHit(identifier, score)
            for identifier, score in sorted(found.items(), key=lambda item: (-item[1], item[0]))[
                :limit
            ]
        ]

    async def delete_generation(self, tenant_id, generation_id):
        _scope(tenant_id)
        _scope(generation_id)
        await self.index.delete(
            namespace=tenant_id,
            filter=self._filter({"generation_id": {"$eq": generation_id}}),
        )

    async def aclose(self):
        await self.index.close()


class PgVectorIndex:
    """Separate PostgreSQL vector table with native async IO and optional HNSW.

    initialize() is an explicit lab/bootstrap operation requiring DDL privileges.
    Production migrations should provision these tables/indexes before workers run.
    """

    def __init__(
        self,
        url: str,
        *,
        dimensions: int,
        embedding_identity: str,
        hnsw: bool = False,
        m: int = 16,
        ef_construction: int = 64,
        ef_search: int = 100,
        exact: bool = False,
    ):
        _configuration(dimensions, embedding_identity)
        if (
            any(type(value) is not int for value in (m, ef_construction, ef_search))
            or not 2 <= m <= 100
            or not 4 <= ef_construction <= 1000
            or not 1 <= ef_search <= 1000
            or ef_construction < 2 * m
        ):
            raise ValueError("HNSW parameters outside allowed bounds")
        self.url, self.dimensions, self.embedding_identity = url, dimensions, embedding_identity
        self.hnsw, self.m = hnsw, m
        self.ef_construction, self.ef_search, self.exact = ef_construction, ef_search, exact

    @asynccontextmanager
    async def _connection(self, *, validate: bool = True):
        async with await psycopg.AsyncConnection.connect(
            self.url,
            connect_timeout=5,
            options="-c statement_timeout=10000",
        ) as connection:
            if validate:
                await self._validate_configuration(connection)
            yield connection

    async def _validate_configuration(self, connection):
        cursor = await connection.execute(
            "SELECT embedding_identity,dimensions FROM io_vector_config WHERE singleton=1"
        )
        if await cursor.fetchone() != (self.embedding_identity, self.dimensions):
            raise ValueError("Vector embedding configuration mismatch; provision a new index")

    async def validate_configuration(self):
        """Read-only startup check; the runtime role does not require DDL privileges."""
        async with self._connection():
            pass

    async def initialize(self):
        async with self._connection(validate=False) as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS io_vector_config "
                "(singleton INTEGER PRIMARY KEY, embedding_identity TEXT NOT NULL, "
                "dimensions INTEGER NOT NULL)"
            )
            await conn.execute(
                "INSERT INTO io_vector_config VALUES (1,%s,%s) ON CONFLICT (singleton) DO NOTHING",
                (self.embedding_identity, self.dimensions),
            )
            await self._validate_configuration(conn)
            # Only validated integers enter DDL; all request values use bound parameters.
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS io_vectors ("
                "tenant_id TEXT NOT NULL, generation_id TEXT NOT NULL, "
                f"chunk_id TEXT NOT NULL, embedding vector({self.dimensions}) NOT NULL, "
                "PRIMARY KEY (tenant_id,generation_id,chunk_id))"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS io_vectors_scope "
                "ON io_vectors (tenant_id,generation_id)"
            )
            if self.hnsw:
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS io_vectors_hnsw ON io_vectors "
                    f"USING hnsw (embedding vector_cosine_ops) WITH "
                    f"(m={self.m}, ef_construction={self.ef_construction})"
                )

    async def upsert(self, tenant_id, generation_id, chunks, vectors):
        _write_inputs(tenant_id, generation_id, chunks, vectors, self.dimensions)
        async with self._connection() as conn:
            for chunk, vector in zip(chunks, vectors, strict=True):
                await conn.execute(
                    "INSERT INTO io_vectors VALUES (%s,%s,%s,%s::vector) "
                    "ON CONFLICT (tenant_id,generation_id,chunk_id) DO UPDATE "
                    "SET embedding=EXCLUDED.embedding",
                    (tenant_id, generation_id, chunk.chunk_id, json.dumps(vector)),
                )

    async def search(self, tenant_id, vector, generation_ids, limit):
        _search_inputs(tenant_id, vector, generation_ids, limit, self.dimensions)
        if not generation_ids:
            return []
        async with self._connection() as conn:
            await conn.execute(
                "SELECT set_config('hnsw.ef_search', %s, true)", (str(self.ef_search),)
            )
            if self.hnsw and not self.exact:
                await conn.execute("SELECT set_config('hnsw.iterative_scan','strict_order',true)")
            # + 0 deliberately prevents matching the HNSW ordering for exact-reference runs.
            ordering = (
                "(embedding <=> %s::vector) + 0" if self.exact else "embedding <=> %s::vector"
            )
            cursor = await conn.execute(
                "SELECT chunk_id, 1 - (embedding <=> %s::vector) FROM io_vectors "
                "WHERE tenant_id=%s AND generation_id=ANY(%s) "
                f"ORDER BY {ordering},chunk_id LIMIT %s",
                (json.dumps(vector), tenant_id, generation_ids, json.dumps(vector), limit),
            )
            return [
                VectorHit(identifier, float(score)) for identifier, score in await cursor.fetchall()
            ]

    async def delete_generation(self, tenant_id, generation_id):
        _scope(tenant_id)
        _scope(generation_id)
        async with self._connection() as conn:
            await conn.execute(
                "DELETE FROM io_vectors WHERE tenant_id=%s AND generation_id=%s",
                (tenant_id, generation_id),
            )

    async def aclose(self):
        # Connections are scoped per operation; a production pool can replace this adapter detail.
        pass
