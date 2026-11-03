from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTOPS_", env_file=".env", extra="ignore")

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
    chunk_tokens: int = Field(default=350, ge=100, le=2000)
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
        return self
