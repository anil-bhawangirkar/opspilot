"""Version-one relational schema; changes require a new Alembic revision."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()
jobs = Table(
    "io_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(80), nullable=False),
    Column("principal_id", String(160), nullable=False),
    Column("groups", JSON, nullable=False),
    Column("kind", String(20), nullable=False),
    Column("idempotency_key", String(160), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("fence", Integer, nullable=False),
    Column("lease_until", Float, nullable=False),
    Column("deadline", Float, nullable=False),
    Column("run_after", Float, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("result", JSON),
    Column("error_code", String(80)),
    Column("event_sequence", Integer, nullable=False, server_default="0"),
    UniqueConstraint("tenant_id", "principal_id", "idempotency_key", name="io_job_intent"),
)
outbox = Table(
    "io_outbox",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("published_at", Float),
)
heads = Table(
    "io_document_heads",
    metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("document_id", String(160), primary_key=True),
    Column("generation_id", String(36)),
    Column("revision", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    Column("access_group", String(80)),
)
versions = Table(
    "io_source_versions",
    metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("document_id", String(160), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("content_hash", String(64), nullable=False),
)
generations = Table(
    "io_generations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(80), nullable=False),
    Column("document_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("embedding_id", String(160), nullable=False),
    Column("expected_revision", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("source_metadata", JSON, nullable=False),
    Column("document_hash", String(64), nullable=False),
)
chunks = Table(
    "io_chunks",
    metadata,
    Column("generation_id", String(36), primary_key=True),
    Column("chunk_id", String(64), primary_key=True),
    Column("tenant_id", String(80), nullable=False),
    Column("document_id", String(160), nullable=False),
    Column("access_group", String(80), nullable=False),
    Column("service", String(80), nullable=False),
    Column("region", String(80), nullable=False),
    Column("release", String(40), nullable=False),
    Column("source_status", String(20), nullable=False),
    Column("valid_from", String(10), nullable=False),
    Column("valid_to", String(10)),
    Column("payload", JSON, nullable=False),
    Column("embedding", JSON, nullable=False),
)
batches = Table(
    "io_embedding_batches",
    metadata,
    Column("generation_id", String(36), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("chunk_ids", JSON, nullable=False),
)
events = Table(
    "io_run_events",
    metadata,
    Column("job_id", String(36), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("event_type", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
)
local_vectors = Table(
    "io_local_vectors",
    metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("generation_id", String(36), primary_key=True),
    Column("chunk_id", String(64), primary_key=True),
    Column("embedding", JSON, nullable=False),
)
Index("io_jobs_ready", jobs.c.status, jobs.c.run_after, jobs.c.lease_until)
Index("io_outbox_pending", outbox.c.published_at, outbox.c.created_at)
Index(
    "io_chunks_scope",
    chunks.c.tenant_id,
    chunks.c.service,
    chunks.c.region,
    chunks.c.release,
    chunks.c.access_group,
)
Index("io_chunks_lookup", chunks.c.tenant_id, chunks.c.chunk_id)
embedding_cache = Table(
    "io_embedding_cache",
    metadata,
    Column("tenant_id", String(80), primary_key=True),
    Column("embedding_id", String(160), primary_key=True),
    Column("content_hash", String(64), primary_key=True),
    Column("embedding", JSON, nullable=False),
)
