"""Application contracts. Infrastructure adapters satisfy them structurally."""

from datetime import date
from typing import Protocol

from incidentops.models import Chunk, Evidence, Principal, Question, SourceDocument


class DocumentChunker(Protocol):
    def split(self, document: SourceDocument) -> list[Chunk]: ...


class ChunkWriter(Protocol):
    def replace_corpus(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...


class EvidenceIndex(Protocol):
    def search(
        self,
        principal: Principal,
        question: Question,
        vector: list[float],
        limit: int,
        *,
        as_of: date | None = None,
    ) -> list[Evidence]: ...


class SourceReader(Protocol):
    def get_chunk(self, principal: Principal, chunk_id: str) -> Chunk | None: ...


class Retriever(Protocol):
    def retrieve(self, principal: Principal, question: Question) -> list[Evidence]: ...
