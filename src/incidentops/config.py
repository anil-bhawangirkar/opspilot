from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCIDENTOPS_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    provider: Literal["fake", "openai"] = "fake"
    database_url: str = "sqlite:///.local/incidentops.db"
    corpus_dir: Path = Path("data/corpus")
    acme_token: str = Field(default="acme-demo-token", repr=False, min_length=12)
    bravo_token: str = Field(default="bravo-demo-token", repr=False, min_length=12)
    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=8, le=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    context_tokens: int = Field(default=1800, ge=30, le=12000)
    chunk_size_chars: int = Field(
        default=350,
        ge=100,
        le=2000,
        validation_alias=AliasChoices("INCIDENTOPS_CHUNK_SIZE_CHARS", "INCIDENTOPS_CHUNK_TOKENS"),
    )
    chunk_overlap_chars: int = Field(default=40, ge=0)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    ingestion_max_chunks: int = Field(default=2000, ge=1, le=10000)
    timeout_seconds: float = Field(default=20, ge=1, le=120)
    max_retries: int = Field(default=1, ge=0, le=3)
    tracing: bool = False
    trace_project: str = "incidentops-increment-1"
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")

    @model_validator(mode="after")
    def distinct_tokens(self):
        if self.acme_token == self.bravo_token:
            raise ValueError("Tenant tokens must be distinct")
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        return self

    @property
    def chunk_tokens(self) -> int:
        """Deprecated source compatibility; current chunking measures characters."""
        return self.chunk_size_chars
