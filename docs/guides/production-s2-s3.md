# V2 durable workflows: setup, architecture and failure lab

## Implemented boundary

This batch implements the S2/S3 foundation and the core retrieval experiment path. It preserves v1 so the learner can compare a synchronous teaching baseline with queued asynchronous work. It does not claim completion of every item in the target production design.

| Implemented | Remaining / explicitly unverified |
|---|---|
| Async job submission, authorized polling and cancellation | Production OIDC, global principal-role revocation and RLS |
| Job/outbox transaction, leases, fencing, heartbeats, bounded retries and recovery | Real broker/DB failover and measured HA |
| Per-document staged generations and optimistic atomic promotion | Physical retention/garbage collection and online schema migration rollout |
| Content-addressed artifacts, local and versioned S3 adapters | Real S3 account/IAM integration and cross-region restore |
| Async pgvector/Pinecone contracts, HNSW controls, BM25/RRF, exact text dedup | Remote vendor benchmarks and a distributed lexical engine |
| Scoped persistent embedding reuse | Redis retrieval/answer cache, global RPM/TPM/cost reservations and fairness |
| Metadata-safe optional LangSmith export and persistent progress events | Token streaming, calibrated semantic judges and observed production monitoring |
| Separate retrieval-only deployment entry point | Service-to-service authentication/routing; workers currently reuse the retrieval module in-process |

Long requests are stored first and picked up by the worker. There is no FastAPI in-memory BackgroundTasks dependency for work durability.

## Architecture and transaction boundaries

```mermaid
flowchart TD
    Client[Authenticated client] --> API[Async v2 API]
    API --> Object[Immutable artifact store]
    API --> Ledger[PostgreSQL jobs and outbox]
    Ledger --> Dispatcher[Outbox dispatcher and lease recovery]
    Dispatcher --> Rabbit[RabbitMQ]
    Rabbit --> Small[Small ingestion pool]
    Rabbit --> Large[Large ingestion pool]
    Rabbit --> Answer[Investigation pool]
    Small --> Stage[Persisted chunk and embedding batches]
    Large --> Stage
    Stage --> Vector[pgvector or Pinecone]
    Vector --> Promote[Atomic active-generation switch]
    Promote --> Ledger
    Answer --> Retrieval[Scoped dense and BM25 retrieval]
    Retrieval --> LLM[Native async model call]
    LLM --> Check[Quote and current-access validation]
    Check --> Ledger
    Ledger --> Poll[Polling and SSE progress]
```

## Plain-English Summary

The database owns accepted intent and visible document generations. The broker wakes workers. Workers checkpoint batches and only publish complete generations; model results are validated and persisted for later authorized polling. Object storage and remote vector writes are separate transaction boundaries.

Worker pseudocode:

```text
accept: authorize -> redact -> immutable artifact -> transaction(job + outbox)
dispatch: publish UUID -> broker confirms -> mark event published
execute: atomic claim/fence -> heartbeat + deadline
  ingestion: pinned artifact/profile -> checkpoint batches -> upsert -> CAS promote
  answer: scoped hybrid search -> bounded async model -> verify quotes/access -> persist
failure: classify -> finite delayed retry or terminal ledger state
recovery: expired lease/lost delivery -> outbox redrive; stale fences cannot commit
```

## 1. Free isolated demo

```bash
uv sync --frozen
uv run incidentops-workflow demo
```

This runs the actual Alembic migration against a temporary SQLite database, stores redacted immutable artifacts, executes five current operator-visible fixture ingestions and one answer job, then demonstrates that running the completed job again does no work. The local vector adapter is persistent exact search, not HNSW. The demo deletes its temporary state on exit and labels results `quality_evidence=false`.

The corpus contains restricted, superseded and future sources too. Demo seeding deliberately excludes them; authorization/freshness behavior has separate tests. It does not quietly grant an operator the security-reviewer role.

## 2. Persistent local path without a broker

Copy `.env.example` to `.env` if needed, preserving any existing local keys. Select fake provider, local objects/vectors and the workflow database URL shown in that example. Then:

```bash
uv run incidentops-workflow migrate
uv run incidentops-workflow seed-local
uv run incidentops-workflow evaluate --output results/v2-dev.json
uv run incidentops-workflow serve
```

`seed-local` is a trusted operator exercise that executes the persisted fixture jobs locally. It uses the configured provider; use fake mode for a free run. It is not a claim that a broker was involved. Public API submissions still return immediately without executing the job.

In another terminal:

```bash
curl -s http://127.0.0.1:8000/v2/answer-jobs \
  -H 'Authorization: Bearer acme-demo-token' \
  -H 'Idempotency-Key: investigation-001' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does PUB-403 mean in release 2.7?"}'
```

The response is `202` with an `id`, queued status and `Location: /v2/jobs/<id>`. Without a worker it stays queued. Execute exactly that persisted job locally:

```bash
uv run incidentops-workflow run YOUR_JOB_ID
curl -s http://127.0.0.1:8000/v2/jobs/YOUR_JOB_ID -H 'Authorization: Bearer acme-demo-token'
curl -N http://127.0.0.1:8000/v2/jobs/YOUR_JOB_ID/events -H 'Authorization: Bearer acme-demo-token'
```

Use the returned `/v2/sources/...` URL with the same token to inspect a citation. Open `/docs` for interactive requests. Stop an old v1 server first if it uses port 8000.

## 3. Real broker and PostgreSQL path using Compose

Requires Docker/Compose. These are development credentials and single-instance services. This topology is not a highly available cluster.

```bash
INCIDENTOPS_PROVIDER=fake INCIDENTOPS_VECTOR_BACKEND=pgvector \
  docker compose --profile workflows up --build -d workflow-api dispatcher ingest-small ingest-large investigations
```

The explicit vector setting overrides a local `.env` selecting the reference adapter. Dependencies run schema/index initialization once before workers/API start. Sources are not embedded automatically during startup. API is on **8002**, RabbitMQ management on **15672**. The existing v1 API can remain separate on 8000.

Queue current fixtures for the actual workers:

```bash
docker compose --profile workflows exec workflow-api incidentops-workflow enqueue-fixtures
```

Submit the earlier curl request using port 8002; poll its Location. Use `docker compose --profile workflows logs dispatcher investigations ingest-small ingest-large` for process health. Logs intentionally do not dump source/model payloads.

The queues have separate pools: `ingestion.small` (2 processes), `ingestion.large` (1), `investigations` (2), with prefetch 1. The server classifies large ingestion using redacted source bytes; clients cannot select an arbitrary queue. This is a bulkhead demonstration, not weighted tenant fairness or a global provider quota. RabbitMQ publisher confirms acknowledge broker acceptance, not successful execution.

Celery's prefork pool handles one task per process slot. Native async IO enables nonblocking waits and parallel branches within a job; it does not make one Celery slot execute unlimited investigations concurrently. Increase measured job capacity through bounded worker slots/replicas and provider quotas. Each task constructs/closes its own runtime/event loop, avoiding cross-loop client reuse at the cost of connection setup. A persistent async worker or managed graph run server is a later alternative if measurements justify it.

The API can persist accepted jobs while the broker is down. The dispatcher must run continuously for publication, delayed retries, deadline expiry and lease recovery. A crash after publish but before marking the outbox can publish twice; the job claim/fence prevents a second active owner. A worker crash after its final DB commit but before acknowledgement produces no repeated business publication.

For the retrieval-only process:

```bash
docker compose --profile workflows up --build -d retrieval
```

Its `/v2/retrieve` and source endpoints are on **8003**. It does not accept job submissions. Workers currently call the same retrieval module locally; remote routing/delegated identity is a later deployment choice, not a hidden RPC in this version.

Stop without deleting persistent volumes: `docker compose --profile workflows down`. Existing volumes retain data. Do not delete volumes to test failover unless you intentionally want data loss in a disposable lab.

## 4. Choosing the vector backend

### pgvector

On the host, configure:

```dotenv
INCIDENTOPS_WORKFLOW_DATABASE_URL=postgresql+asyncpg://incidentops:incidentops@localhost:5432/incidentops
INCIDENTOPS_VECTOR_BACKEND=pgvector
INCIDENTOPS_VECTOR_DATABASE_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops
INCIDENTOPS_HNSW=true
INCIDENTOPS_HNSW_EF_SEARCH=100
```

Run `incidentops-workflow migrate`, then `incidentops-workflow index-init`. The latter is an explicit lab bootstrap with DDL privileges for the vector table/index; application operations validate embedding identity/dimension using read-only checks. Production rollout should move vector provisioning into reviewed migrations and use distinct migration/runtime DB roles. Do not grant DDL rights merely to bypass a missing bootstrap.

`INCIDENTOPS_VECTOR_EXACT=true` deliberately requests the exact reference search, including when an HNSW index already exists. Disabling the HNSW creation flag alone does not drop an existing index; inspect query plans rather than assuming planner behavior from a configuration label.

### Pinecone

Use an existing compatible cosine index with the correct dimensions (256 for the fake provider; default 1536 for the opt-in OpenAI embedding configuration):

```dotenv
INCIDENTOPS_VECTOR_BACKEND=pinecone
INCIDENTOPS_PINECONE_HOST=https://your-index-host
PINECONE_API_KEY=your-local-key
```

PostgreSQL remains the metadata/job authority. Pinecone uses a tenant namespace, embedding-profile filter, generation filter and deterministic generation/chunk IDs. The application hydrates only authorized active chunks from the relational authority and rechecks them before release. Pinecone write acknowledgement does not promise immediate query visibility; temporary missing dense hits must not cause permission broadening. The lexical branch may still find authorized text.

Provisioning a paid index is outside automated tests. No source text or bearer token is stored as remote vector metadata. Never reuse an embedding space with another model/dimension silently; provision/reindex deliberately.

## 5. S3 files and manifests

Default local artifacts are redacted JSON under a content-addressed tenant key. Filesystem writes are atomic/no-overwrite and checksums are verified on reads. Compose shares a local volume between its containers; that is a single-host lab choice, not multi-host HA storage.

For actual S3 on a correctly configured host or ECS task role:

```dotenv
INCIDENTOPS_OBJECT_BACKEND=s3
INCIDENTOPS_S3_BUCKET=your-private-versioned-bucket
```

The bucket must have versioning enabled. The adapter uses conditional writes, persists exact VersionId and verifies an application SHA-256. It rejects unversioned writes rather than silently accepting mutable provenance. Use standard boto3 IAM/region configuration. The development Compose file does not mount your host AWS credentials automatically; configure a proper role or an explicit local credential mechanism when testing S3.

Upload and job acceptance are not one transaction. Failed DB registration can leave an orphan immutable object; failed ingestion can leave staging generations/vectors. Retention, reconciliation and physical garbage collection are not implemented yet. Deletion jobs logically retire the active document; they do not claim physical erasure from every object/cache/backup.

## 6. Progressive updates, duplicates and idempotency

Post `document` plus optional `expected_revision` to `/v2/ingestion-jobs`. Each job pins source artifact, splitter profile, embedding identity, batch size and expected current revision. The same tenant/principal/key/intent returns the same job; a different intent under that key is a 409.

Only one document's active-generation pointer changes. Other tenants/documents remain untouched. A source version cannot be reused with changed text. A stale concurrent publisher loses the revision compare-and-swap and fails; it does not overwrite a newer document. Metadata/ACL changes can use a new generation with unchanged source text; current ACL checks gate both write and read paths.

Embedding batches checkpoint in the relational store. After restart, completed batches are reused, and unacknowledged remote vector upserts can safely repeat. Unchanged text reuses embeddings keyed by tenant, embedding profile and content hash; the cache does not cross tenants. This cache is a derived-text computation cache, not an answer cache or permission decision.

Crash before saving a provider response may still repeat paid work. Checkpoints, idempotent vector IDs and job ledgers do not provide exactly-once provider billing. Answer generation is retried as a bounded job; persistent LangGraph node-level checkpoints are not part of v2 yet. Source/quote authorization is rechecked when returning a stored answer after a later source change or revocation, including duplicate provenance.

## 7. Failure lab

```bash
uv run pytest -q tests/durable tests/test_hybrid_retrieval.py tests/test_vector_adapters.py tests/test_object_storage.py
```

Observe each invariant:

- Reuse a submission key with another question: 409.
- Kill a worker after a persisted embedding batch: recovery reuses it.
- Let a lease expire and claim again: old fencing token cannot publish/complete.
- Delay a retry: outbox dispatch waits until due; it cannot be acknowledged too early and stranded.
- Lose a delivery before claim: periodic reconciliation redispatches accepted work, bounded by deadline.
- Update a security document with a forged incoming operators ACL: denied by current-head checks.
- Revoke a duplicate source after an answer: polling removes its provenance while preserving a still-valid representative.
- Replay more than 100 progress events: all pages drain through the final event; expiry/exhaustion are recorded once.
- Cancel a long model await or lose its heartbeat: no final mutation is accepted after ownership loss. Remote work already started may still be billed.
- Send an oversized/chunked body: 413 before JSON parsing. Default HTTP concurrency is capped per process, not globally across replicas.

The worker owns job retries; OpenAI SDK retries are disabled for v2 to avoid multiplying retry trees. Transient failures use bounded exponential jitter; config/schema/conflict errors are terminal. Failed jobs remain in the ledger for inspection; a broker dead-letter/redrive administration API is still future work.

## 8. Experiments and service verification

After local seeding or completed worker ingestion:

```bash
uv run incidentops-workflow evaluate --output results/v2-dev.json
uv run incidentops-workflow evaluate --dataset data/eval/holdout.jsonl --output results/v2-holdout.json
```

Evaluation is a local operator command that executes persisted jobs; public requests remain asynchronous. It records retrieval settings, backend, answerability, required-document coverage, failure status and measured timings. Fake results are plumbing only. Billed tokens and dollar cost remain null until actual usage accounting is implemented.

Optional real-service tests require disposable services:

```bash
INCIDENTOPS_TEST_POSTGRES_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops_test uv run pytest -q -m postgres
INCIDENTOPS_TEST_BROKER_URL=amqp://incidentops:incidentops@localhost:5672// uv run pytest -q -m broker
```

Create the test database first with `docker compose exec db createdb -U incidentops incidentops_test` (skip if it already exists). PostgreSQL tests provision test tables/indexes; broker tests launch their own separate worker process. Do not use production resources. CI includes separate PostgreSQL and RabbitMQ jobs, but supplying a workflow is not evidence it ran. See [actual verification](../verification-v2.md).

## Learning order

Read `durable/schema_v1.py` and migration 0001, `repository.py` transactions, object storage, runtime submission/worker methods, broker dispatch and the async API. Then read retrieval contracts/indexes/hybrid ranking. Explain why each commit boundary exists before adding shared quotas, graph interrupts or recovery actions. [ADR 0003](../adr/0003-durable-jobs-and-generations.md) records the tradeoffs.
