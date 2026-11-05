"""Persistent exact vector adapter for the local lab, not an ANN production service."""

import asyncio
import math

from sqlalchemy import delete, select

from incidentops.durable.repository import Conflict
from incidentops.durable.schema import local_vectors
from incidentops.retrieval.contracts import VectorHit
from incidentops.store import cosine


class LocalVectorIndex:
    def __init__(self, repository, dimensions):
        self.repo, self.dimensions = repository, dimensions

    def _validate(self, vector):
        if (
            len(vector) != self.dimensions
            or not any(vector)
            or not all(math.isfinite(x) for x in vector)
        ):
            raise ValueError("Invalid local vector")

    async def upsert(self, tenant_id, generation_id, chunks, vectors):
        if len(chunks) != len(vectors):
            raise ValueError("Invalid vector batch count")
        async with self.repo.engine.begin() as conn:
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._validate(vector)
                if chunk.tenant_id != tenant_id:
                    raise ValueError("Cross-tenant vector upsert")
                values = dict(
                    tenant_id=tenant_id,
                    generation_id=generation_id,
                    chunk_id=chunk.chunk_id,
                    embedding=vector,
                )
                await self.repo._ignore(
                    conn, local_vectors, values, ["tenant_id", "generation_id", "chunk_id"]
                )
                stored = (
                    await conn.execute(
                        select(local_vectors.c.embedding).where(
                            local_vectors.c.tenant_id == tenant_id,
                            local_vectors.c.generation_id == generation_id,
                            local_vectors.c.chunk_id == chunk.chunk_id,
                        )
                    )
                ).scalar_one()
                if stored != vector:
                    raise Conflict("Vector changed during replay")

    async def search(self, tenant_id, vector, generation_ids, limit):
        self._validate(vector)
        if not generation_ids:
            return []
        async with self.repo.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(local_vectors).where(
                            local_vectors.c.tenant_id == tenant_id,
                            local_vectors.c.generation_id.in_(generation_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )

        def rank():
            result = [VectorHit(row["chunk_id"], cosine(vector, row["embedding"])) for row in rows]
            return sorted(result, key=lambda hit: (-hit.score, hit.chunk_id))[:limit]

        return await asyncio.to_thread(rank)

    async def delete_generation(self, tenant_id, generation_id):
        async with self.repo.engine.begin() as conn:
            await conn.execute(
                delete(local_vectors).where(
                    local_vectors.c.tenant_id == tenant_id,
                    local_vectors.c.generation_id == generation_id,
                )
            )

    async def aclose(self):
        pass  # Repository lifecycle is owned by the runtime.
