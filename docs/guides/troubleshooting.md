# Setup and workflow troubleshooting

Use `incidentops-workflow` for current v2 jobs. `incidentops` is the [legacy v1 path](legacy-v1.md); their databases and source stores are separate. Follow [v2 setup](production-s2-s3.md) before debugging retrieval.

| Symptom | Likely cause | Next step |
|---|---|---|
| `uv` missing | Package manager unavailable | Follow the installation link in README and reopen the terminal |
| `uv sync` download/cache error | Network/proxy/certificate or cache permissions | Restore normal package access; for a restricted cache use `UV_CACHE_DIR=/private/tmp/incidentops-uv-cache` |
| `No module named app` | Command targets a package this repository does not contain | From the repo root run `uv sync --frozen`, then `uv run incidentops-workflow demo`; the package is `incidentops` |
| No workflow tables | Migration has not run for this URL | Run `uv run incidentops-workflow migrate` against the intended workflow database |
| Job stays `queued` | Accepted work has no worker, no dispatcher or unavailable broker | For the no-broker exercise run `incidentops-workflow run JOB_ID`; for Compose inspect dispatcher and the appropriate worker pool |
| Job stays `retry` | Backoff is not due or a transient dependency is still unavailable | Inspect safe error code, deadline and dispatcher/worker health; do not create infinite retries |
| Running job after worker crash | Lease must expire before takeover | Keep dispatcher/reaper running; old fencing tokens cannot complete after takeover |
| No evidence after successful v1 ingest | V2 source store is still empty | V2: `migrate`, then `seed-local` in fake/local mode, or `enqueue-fixtures` with actual workers |
| No evidence with v2 sources | Wrong scope, date/status, embedding profile or context budget | Inspect current source metadata; defaults are west/image-publisher/2.7; do not broaden tenant permissions to fill top-k |
| Authorized corpus exceeds cap | In-memory BM25 reference limit reached | Use the bounded lab corpus; evaluate an indexed lexical adapter before increasing scale |
| 409 on submission | Same key has another intent or revision conflict | Reuse a key only for identical intent; deliberate updates need a new key and current expected revision |
| Ingestion job fails on profile | Splitter/batch/embedding configuration changed after acceptance | Restore pinned settings for replay or submit a deliberate new job |
| Source version conflict | Existing source version's redacted text changed | Publish a new version instead of rewriting the old version |
| Embedding identity/dimension mismatch | Existing vectors belong to another embedding space | Provision a new index/database and reingest; do not relabel vectors |
| Missing pgvector table/index | Explicit vector bootstrap not run | Run `index-init` with the reviewed initialization role after migrations |
| HNSW flag changed but plan did not | Existing index remains or planner chose another plan | Compare `VECTOR_EXACT=true`; inspect EXPLAIN; changing creation flags does not drop/rebuild indexes |
| Pinecone temporarily misses recent dense hits | Remote write visibility is not immediate | Measure freshness and design a barrier; keep authorization filters intact |
| S3 rejects artifact storage | Bucket unversioned, IAM/config invalid or checksum failure | Use a private versioning-enabled bucket and valid role; inspect configuration without printing payloads/secrets |
| Stored answer becomes insufficient evidence | Cited source updated, expired, retired or access changed | Ask again against current evidence; inspect the source using the same principal |
| 401 / 403 | Incorrect token / insufficient scope | Use configured demo identity and intended group; restart after settings changes |
| 404 for job/source | Missing or outside tenant/principal/current source scope | Use original authorized identity; 404 does not reveal another tenant's existence |
| 413 | Body exceeds pre-parse limit | Use a source within the lab's request limit |
| SSE connection closes | Bounded connection interval or terminal job | Reconnect with `?after=LAST_SEQUENCE`; progress is persisted |
| Port conflict | Host v1/v2 API uses the same default port 8000 | Choose `serve --port 8004`, or use Compose v2 on 8002 and retrieval-only on 8003 |
| Missing LangSmith trace | Tracing disabled, configuration wrong or export unavailable | Set application tracing/key/project and rerun; local tests never prove remote delivery |
| `LANGSMITH_TRACING=true` gives no raw prompts | Intentional application privacy boundary | Inspect synthetic prompts in a local debugger; use allowlisted summaries |
| PostgreSQL/RabbitMQ tests skipped | Disposable service URL absent | Follow the optional test commands in the v2 setup guide |
| Docker unavailable | No runtime/Compose installed | Use the local SQLite/fake workflow; actual service integration remains unverified |

Configuration comes from `.env` and environment variables, with environment variables taking priority. Preserve existing `.env` keys when copying examples; do not overwrite the file. Settings are captured at process startup, so restart API/worker processes after changes. Demo forces temporary fake/local execution; persistent operator commands use configured backends and may make paid calls if configured live.

The v1-only `provider_error`, `retrieval_error` and `invalid_output` response reasons are explained in its [learning guide](learning-lab.md). V2 dependency/schema exceptions produce safe retry/failed job states; a completed insufficient-evidence assessment is different from job failure.

Stop containers while keeping volumes with `docker compose --profile workflows down`. For a clean experiment choose a new workflow database/object directory and vector index deliberately. Do not delete existing `.local`, object volumes or evaluation reports as routine cleanup. See [low-level design](../low-level-design.md) for state, source and tracing rules.
