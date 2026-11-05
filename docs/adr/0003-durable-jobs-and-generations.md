# ADR 0003: Durable asynchronous jobs and per-document index generations

Status: accepted and implemented for the v2 foundation, 2026-11-05. Remote-service and HA acceptance remain separate. [Setup/failure lab](../guides/production-s2-s3.md), [retrieval lab](../guides/retrieval-lab.md).

## Problem

The v1 fixture writer replaces a global snapshot, and an HTTP process owns the entire model call. That is unsuitable for multi-tenant background ingestion, process crashes, slow models or retries. Broker acknowledgement cannot decide which worker may publish a document or answer.

## Decision

Keep v1 for learning; introduce an async `/v2` API and schema. PostgreSQL owns durable jobs, source identity, access, embedding checkpoints and active generations. SQLite exercises the same contract locally. SQLAlchemy uses async connections; Alembic owns relational changes. Migration 0001 imports frozen `schema_v1.py`, so later model edits cannot change an old migration's clean-install behavior.

Accept a job and outbox event in one transaction. Scope idempotency by tenant/principal/key and reject another canonical intent under that key. Dispatcher publishes only the UUID to a server-selected Celery/RabbitMQ queue, then marks broker acceptance. Duplicate publication is allowed; atomic claims advance attempts/fences and acquire bounded leases. A reaper recovers expired leases and redrives work lost before claim. Completion/checkpoint/promotion verifies current ownership.

Separate small ingestion, large ingestion and investigation pools, with prefetch 1. CPU splitting/BM25 and bounded synchronous SDK IO move off the event loop. Embedding/chat calls use native async APIs. Jobs have deadlines, heartbeats and finite retries; OpenAI SDK retries are disabled in v2 to keep one job retry owner. Semaphores are process-local, not distributed quotas.

Specifically, OpenAI SDK retries are disabled; boto3 retains finite SDK retries for its bounded synchronous calls. The shared job classifier recognizes known vendor status/codes and database SQLSTATEs without inspecting raw messages. Unknown errors fail terminally. Retry-After propagation and a universal provider gateway remain later work. Celery uses a fresh runtime/event loop per task, trading setup overhead for explicit resource ownership; its prefork slots bound cross-job concurrency.

Store redacted immutable artifacts before job registration. Local content-addressed storage uses atomic no-overwrite writes; S3 requires versioning and retains exact VersionId plus application SHA-256. ETag is not a universal content hash. Jobs pin the source reference and ingestion profile.

Stage chunk/embedding batches by generation. Persist each complete batch atomically, reuse it on recovery, and reuse unchanged embeddings within tenant/model/content scope. Upsert remote vectors with deterministic IDs before atomically switching one document's active pointer using an expected-revision CAS. Complete ingestion in that same authoritative transaction. Source text is immutable within a version. Current document ACLs gate writes/reads independently of submitted metadata.

Use an async vector-index protocol for local exact, pgvector and Pinecone adapters. Remote hits hydrate from the current authorized relational set; they cannot supply new evidence text or permission. Use BM25+dense RRF and exact content deduplication under a bounded policy. Keep optional reranking visibly disabled until selected/evaluated.

Persist validated answers and monotonic safe progress events. Polling rechecks source identity/access, including duplicate provenance. SSE replays by cursor through all pages and the terminal event. API returns 202 before inference; neither FastAPI BackgroundTasks nor a local dictionary is the job authority.

## Alternatives

| Alternative | Tradeoff / reason deferred |
|---|---|
| FastAPI background tasks | Convenient, but accepted work is lost if the API dies |
| Broker as the only job ledger | Delivery cannot atomically own document revisions, cancellation, access or provenance |
| One transaction across S3/vector/broker/Postgres | No shared transaction here; use explicit stages, replay and reconciliation |
| Temporal or managed LangGraph run server | Useful alternatives when durable workflow operations dominate; avoid competing orchestration ownership |
| Kafka/Flink | Useful for event logs/continuous transforms; this workload currently needs jobs and interactive search |
| All authority in a vector service | Simpler infrastructure, but transactional source/access/job semantics still need deliberate evaluation |

## Limits and consequences

At-least-once delivery and idempotent publication do not prevent repeated billing after a crash before checkpoint. Job/batch checkpoints are not LangGraph node checkpoints. No human approval, action executor, simulator, graph resume authorization or exactly-once external action is implemented by this ADR.

Upload and job registration can leave orphan artifacts. Old/failed generations remain retained; deletion is logical. Physical GC, retention and reconciliation are later work. Pinecone is eventually consistent; promotion does not prove immediate dense visibility. BM25 is a capped in-memory reference. Pgvector opens bounded-time connections per operation rather than using a dedicated vector pool.

Demo tokens and captured groups are not a production identity/revocation service. Document access is rechecked, but global role revocation/OIDC/RLS remain future work. Leases use application wall-clock time; production needs database-time semantics or a documented clock-skew budget. Redis quotas, queue admission/fairness and cost reservations are not supplied by async IO.

Compose is a single PostgreSQL, classic-queue RabbitMQ and shared local volume lab. Durable queues are not replicated quorum queues. No multi-zone failover, backup restore, rolling upgrade or load SLO has been demonstrated.

## Evidence

Tests cover independent-connection submission/claim races, delayed retries, lost-delivery reconciliation, fences, generation invisibility/CAS, current ACL enforcement, immutable versions/hashes, cross-runtime progress, batch recovery, cancellation, request bounds, safe traces and SSE replay. Adapter stubs establish contracts only. Real PostgreSQL/RabbitMQ tests are opt-in; actual results and unrun integrations are in [verification-v2.md](../verification-v2.md).

Sources: [Celery task semantics](https://docs.celeryq.dev/en/stable/userguide/tasks.html), [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html), [SQLAlchemy async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) and [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence). They explain mechanisms, not this project's performance.
