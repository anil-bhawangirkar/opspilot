from dataclasses import dataclass
from typing import Protocol

from incidentops.models import Chunk, Evidence


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    score: float


class AsyncVectorIndex(Protocol):
    async def upsert(
        self,
        tenant_id: str,
        generation_id: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None: ...

    async def search(
        self,
        tenant_id: str,
        vector: list[float],
        generation_ids: list[str],
        limit: int,
    ) -> list[VectorHit]: ...

    async def delete_generation(self, tenant_id: str, generation_id: str) -> None: ...

    async def aclose(self) -> None: ...


class AsyncReranker(Protocol):
    async def rerank(self, question: str, candidates: list[Evidence]) -> list[Evidence]: ...
