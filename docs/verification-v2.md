# V2 verification record

Checked 2026-11-05 in the working checkout. This records the S2/S3 durable workflow and core retrieval batch, including S1 regressions. Nothing was deployed, pushed or tested against paid providers. Unrelated `.idea/` files were preserved.

## Fresh command evidence

| Command | Observed result |
|---|---|
| `.venv/bin/ruff check .` | Pass |
| `.venv/bin/ruff format --check .` | 87 files already formatted |
| `.venv/bin/python -m pytest -q` | **173 passed, 4 skipped**, 3.92 seconds in this run |
| `UV_CACHE_DIR=/private/tmp/incidentops-uv-cache uv lock --check --offline` | Lock consistent, 97 packages resolved |
| `UV_CACHE_DIR=/private/tmp/incidentops-uv-cache uv sync --frozen --offline` | Pass |
| `UV_CACHE_DIR=/private/tmp/incidentops-uv-cache uv build --offline` | Source archive and wheel built |
| `.venv/bin/python -m incidentops.durable.cli demo` | Real SQLite migration; 5 fixture jobs succeeded; answer produced; duplicate execution returned false |

The regression suite includes actual local filesystem races/tamper checks, independent SQL connections, persisted state reopened through another runtime, actual resource cleanup, ASGI requests and SDK contract stubs. It is not a distributed load or failover test. The CLI failure test was first observed failing, then passed after checking the terminal job status during local seeding.

The dependency lock includes LangChain 1.4.8, LangGraph 1.2.19, text splitters 1.1.3, SQLAlchemy 2.1.8, Alembic 1.20.1, Celery 5.6.3, Pinecone 9.1.0, boto3 1.43.124 and rank-bm25 0.2.2. Use `uv.lock` as the exact complete record. The Pinecone adapter uses the locked 9.x `IndexAsyncio` API; newer major SDK documentation is not a compatibility guarantee. SDK construction/closure was checked without remote calls.

## Persistent local workflow and evaluation exercise

An isolated temporary database under a previously nonexistent parent directory was used with explicit fake provider, tracing disabled and local vectors/objects. Actual subprocess commands ran migrations, `seed-local`, `seed-local` again and both evaluations. This checks the persistent operator path and repeat submission without modifying the user's existing `.local` data.

Reports were written to ignored `results/verification-v2-dev.json` and `results/verification-v2-holdout.json`:

| Dataset | Cases | Fixture assertions | Recorded execution p50 / p95 |
|---|---|---|---|
| Development | 8 | All pass | 7.44 / 11.02 ms |
| Holdout layout | 2 | All pass | 5.00 / 12.48 ms |

Both explicitly report `quality_evidence=false`. These timings are measured local fake-provider execution, not model latency, queue latency, HNSW performance or a production SLO. With two held-out cases, a percentile is especially uninformative. The scripted generator knows fixture intents; passing does not establish semantic answer quality or generalization. Actual tokens/cost are null, reranker is disabled. No LangSmith network traces were exported; safe trace payloads were captured in tests.

## Failure behavior exercised

- Same-key concurrent submissions through independent connections produce one job/outbox record; conflicting intent is rejected.
- Simultaneous claims have one owner. An expired owner's old fence cannot complete or publish.
- Retry events are not dispatched before due; lost pre-claim delivery is eventually redriven; expiry/exhaustion emits terminal progress once.
- Staged data stays invisible. Promotion changes one document pointer and rejects a stale revision. Another tenant/document survives.
- Reused source versions with changed text, forged document hashes and mismatched embedding spaces are rejected.
- A failure after persisted embedding batches reuses those batches and repeats vector upserts safely. Unchanged text reuses embeddings within the tenant; cross-tenant reuse is prevented.
- Current ACL changes invalidate stored answer release and filter revoked duplicate provenance. An incoming forged ACL cannot bypass current write access.
- Long provider awaits are cancelled after ownership loss; cancellation cannot commit a result under an old lease.
- Oversized bodies, including chunked requests, fail before JSON parsing. SSE drains multiple pages through the terminal event.
- Redaction, structured quote validation, source authorization and safe tracing regressions still pass.
- Partial runtime construction closes actual earlier HTTP clients, stores and repository resources.
- Retry classification uses known OpenAI/Pinecone/HTTP status, S3 code/status and database SQLSTATE rather than raw messages. End-to-end job tests show provider 503 retries and provider 403 terminates without persisting sensitive exception text.

These are selected contract/failure tests, not proof of every possible interleaving. A provider may continue/bill work after a client cancellation or crash.

## What did not run

The four skipped tests require real services: legacy PostgreSQL, the new pgvector HNSW/exact adapter, PostgreSQL workflow migration/publication and separate-process RabbitMQ dispatch. `docker`, `psql` and `postgres` were not found in this environment. Compose configuration was checked statically; images were not built or launched. GitHub CI service jobs were authored but were not executed here.

Pinecone requests were checked with fake SDK responses; S3 requests with botocore Stubber plus actual local-store tests. No real Pinecone consistency, S3 versioning/IAM, OpenAI quality/billing or LangSmith service behavior was measured. Separate optional commands are in the [setup guide](guides/production-s2-s3.md#8-experiments-and-service-verification).

## Remaining production work

This batch is a foundation for the next experiments. It does not demonstrate HA: Compose uses a single database, single classic-queue broker and single-host object volume. Multi-zone failover, replicated broker design, backups/restore, rolling upgrades and capacity testing remain open.

Also pending: global Redis RPM/TPM admission and atomic cost reservations; queue-depth/byte quotas, fair tenant scheduling and circuit breakers; production identity/global role revocation/RLS; a pooled remote retrieval client with delegated identity; distributed lexical search, semantic reranking and calibrated judges; full corpus/model/prompt experiment snapshots; Pinecone visibility barriers; physical deletion/GC; database-clock leases; durable LangGraph node checkpoints, memory/interrupts, simulator diagnostics and action reconciliation. Progress SSE is implemented; model token streaming is not.

Read [ADR 0003](adr/0003-durable-jobs-and-generations.md) for transaction guarantees and [the retrieval lab](guides/retrieval-lab.md) for experiments and interview answers. The [living plan](plans/production-evolution/2026-11-05-execplan.md) tracks the next boundary.

## Documentation and cleanup follow-up

The README now leads with v2 setup. Architecture includes the current component flow, ingestion and answer sequence diagrams; low-level design includes actual schemas, interfaces, job state transitions and failure rules. The production design has an implemented/pending status matrix and current table names. V1 setup moved to a labelled [legacy guide](guides/legacy-v1.md), and historical plans/results are explicitly marked.

Fresh checks after these edits: **173 passed, 4 skipped** in 3.84 seconds; Ruff lint/format and `git diff --check` pass. Both CLI demos run successfully. Guide audit reports no missing links or diagram summaries; all README/docs local file links and heading anchors resolve. Mermaid source blocks were checked with the docs; no Mermaid renderer/parser was installed, so rendered visual layout was not verified.

A static import/entry-point/migration/test audit covered 36 source modules with no unreferenced module candidates. All remain used by v2, the retained v1 path or tests. Deleted 87 generated files (1,040,524 bytes): `dist/`, pytest/Ruff caches and eight `__pycache__` directories under source/tests/migrations. Added build-output ignore rules and Docker-context exclusions. Preserved `.env`, `.local`, `results`, `.venv`, `.idea`, migrations, tests, fixtures and decision/evidence history. This follow-up changed documentation/ignore rules, not application behavior.
