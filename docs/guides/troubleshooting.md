# Setup troubleshooting

| Symptom | Likely cause | Next step |
|---|---|---|
| `uv` missing | Package manager not installed | Follow the official uv installation link in README; reopen terminal |
| `uv sync` cannot resolve/download | Offline network, proxy or certificate configuration | Resolve normal package access; do not disable TLS validation |
| Permission error creating uv cache | Restricted workspace | Set `UV_CACHE_DIR=/private/tmp/incidentops-uv-cache` for your command |
| `no_evidence` for all questions | Empty index or wrong scope/budget | Run `uv run incidentops ingest`; use west/image-publisher/2.7; inspect settings |
| `retrieval_error` | Embedding provider/store failed | Check API key, model access, DB availability and embedding space; raw exceptions are intentionally not printed |
| `provider_error` | Timeout, model refusal handling or provider failure | Retry a known case; confirm model supports structured output; inspect safe traces |
| `invalid_output` | Provider response did not satisfy schema | Inspect synthetic local prompt/output in a debugger; do not weaken citation checks to get a pass |
| `invalid_grounding` | Quote absent, citation unknown, source changed or access revoked | Independently inspect the source under the same principal |
| Embedding configuration error at startup | Existing index was created with another provider/model/dimension | Use a new database and reingest; never relabel existing vectors |
| Version conflict during ingestion | Same document version has edited text | Restore the old content or create a new version and supersede the old one |
| 401 from API | Missing/incorrect configured token | Use the local Acme/Bravo token; restart after settings changes |
| 404 for a citation | Wrong tenant/group, source removed, future or superseded | Use correct credentials; a 404 intentionally does not reveal which condition occurred |
| Port already in use | Local and container APIs both use 8000 | Stop one, or start local CLI with `serve --port 8001` |
| No LangSmith trace | Tracing disabled, key/project wrong, export unavailable | Set application tracing and key in `.env`; rerun a question; tests do not contact LangSmith |
| `LANGSMITH_TRACING=true` gives no raw traces | Intentional application privacy boundary | Use the application-owned summary trace; see LLD |
| PostgreSQL test skipped | No explicit disposable test URL | Follow README's separate `incidentops_test` database setup |
| Docker unavailable | No Docker runtime/Compose installed | Use the free SQLite path first; PostgreSQL remains unverified until its actual test runs |

Configuration loads from `.env` and environment variables, with environment variables taking priority. `.env` is ignored by Git. OpenAI and LangSmith keys are secret-valued settings and are passed explicitly to their clients; copying a key into a source file is unnecessary.

The process snapshots settings at startup. Restart after edits. `demo` deliberately ignores live-provider/database/tracing settings and always runs offline.

To stop containers while retaining the lab database, run `docker compose down`. To reset the fake offline index safely, point `INCIDENTOPS_DATABASE_URL` at a fresh SQLite filename and ingest. Keep old databases if you need to inspect prior experiments; do not confuse their embedding configurations.
