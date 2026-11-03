from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    principal_id: str
    groups: tuple[str, ...]


class Question(StrictModel):
    question: str = Field(min_length=3, max_length=2000, pattern=r"\S")
    service: str = Field(default="image-publisher", min_length=1, max_length=80)
    region: str = Field(default="west", min_length=1, max_length=80)
    release: str = Field(default="2.7", min_length=1, max_length=40)


class SourceDocument(StrictModel):
    document_id: str = Field(pattern=r"^[a-z0-9-]+$")
    tenant_id: Literal["acme", "bravo"]
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    service: str
    region: str
    release: str
    access_group: str
    status: Literal["active", "superseded"]
    effective_from: date
    effective_to: date | None
    text: str = Field(min_length=1, max_length=100000)


class Chunk(SourceDocument):
    chunk_id: str
    content_hash: str
    document_hash: str
    start_offset: int
    end_offset: int


class Evidence(StrictModel):
    chunk: Chunk
    score: float


class ProposedClaim(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=150)
    quote: str = Field(min_length=10, max_length=2000)


class Draft(StrictModel):
    answerable: bool
    claims: list[ProposedClaim] = Field(max_length=5)


class Citation(StrictModel):
    chunk_id: str
    document_id: str
    version: int
    title: str
    content_hash: str
    source_url: str

    @classmethod
    def from_chunk(cls, chunk: Chunk):
        return cls(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            version=chunk.version,
            title=chunk.title,
            content_hash=chunk.content_hash,
            source_url=f"/v1/sources/{chunk.chunk_id}",
        )


class Claim(StrictModel):
    quote: str
    citation: Citation


class Metrics(StrictModel):
    latency_ms: float
    context_tokens: int = Field(
        description="Conservative UTF-8 byte upper bound; not billed tokens"
    )
    retrieved_chunks: int
    prompt_version: str


class Assessment(StrictModel):
    run_id: str
    status: Literal["answered", "insufficient_evidence"]
    mode: Literal["fake", "openai"]
    reason: Literal[
        "verified_quotes",
        "no_evidence",
        "not_answerable",
        "invalid_grounding",
        "invalid_output",
        "provider_error",
        "retrieval_error",
    ]
    claims: list[Claim]
    retrieved: list[Citation]
    metrics: Metrics
