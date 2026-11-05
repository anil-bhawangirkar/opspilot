from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator

from incidentops.config import Settings


class WorkflowSettings(Settings):
    workflow_database_url: str = "sqlite+aiosqlite:///.local/workflows.db"
    vector_backend: Literal["local", "pgvector", "pinecone"] = "local"
    vector_database_url: str | None = None
    object_backend: Literal["local", "s3"] = "local"
    object_root: Path = Path(".local/objects")
    s3_bucket: str | None = None
    pinecone_host: str | None = None
    pinecone_api_key: SecretStr | None = Field(default=None, validation_alias="PINECONE_API_KEY")
    broker_url: SecretStr = SecretStr("amqp://guest:guest@127.0.0.1:5672//")
    lease_seconds: int = Field(default=60, ge=6, le=600)
    job_deadline_seconds: int = Field(default=300, ge=1, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    dense_k: int = Field(default=40, ge=1, le=1000)
    lexical_k: int = Field(default=40, ge=1, le=1000)
    rerank_k: int = Field(default=20, ge=1, le=1000)
    final_k: int = Field(default=5, ge=1, le=1000)
    rrf_constant: int = Field(default=60, ge=1)
    retrieval_max_chunks: int = Field(default=2000, ge=1, le=10000)
    hnsw: bool = False
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    vector_exact: bool = False
    provider_concurrency: int = Field(default=4, ge=1, le=100)
    max_input_token_bound: int = Field(default=8000, ge=1000, le=50000)
    max_request_bytes: int = Field(default=512000, ge=1024, le=4000000)
    http_concurrency: int = Field(default=64, ge=1, le=1000)

    @model_validator(mode="after")
    def check_workflow(self):
        if self.final_k > self.rerank_k:
            raise ValueError("final_k must not exceed rerank_k")
        if self.vector_backend == "pgvector" and not self.vector_database_url:
            raise ValueError("vector_database_url is required")
        if self.vector_backend == "pinecone" and not (self.pinecone_host and self.pinecone_api_key):
            raise ValueError("Pinecone host and API key are required")
        if self.object_backend == "s3" and not self.s3_bucket:
            raise ValueError("s3_bucket is required")
        return self
