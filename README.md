# IncidentOps

**An evidence-grounded cloud incident investigation learning project.** Built for a senior cloud engineer studying production AI decisions and preparing for Staff/Principal interviews.

The fictional NimbusCloud image publisher starts failing after release 2.7. Runbooks disagree, another tenant has a similar error with a different explanation, and rollback has a migration prerequisite. IncidentOps finds **applicable, authorized documentation** and exposes supporting evidence.

**This checkout implements increment 1 only:** single-turn, extractive RAG with verified source quotes, explicit abstention and privacy-limited traces. It does not inspect live infrastructure, determine an actual incident root cause, or execute recovery actions. The [seven-increment roadmap](docs/roadmap.md) maps the remaining requested topics to later exercises.

## Available paths

| Path | Components | What it proves |
|---|---|---|
| Free offline default | FastAPI, LangChain interfaces, LangGraph, SQLite exact vector search, hashed embeddings and scripted quote selector | Repeatable application and policy behavior; **not LLM quality** |
| PostgreSQL lab | Same application with PostgreSQL and pgvector exact cosine search | Real SQL/vector integration when its integration test runs |
| Opt-in live model | OpenAI embeddings and structured generation through LangChain; optional LangSmith | Model behavior on the small synthetic dataset you actually evaluate |

Eight documents cover two tenants, superseded and future runbooks, restricted notes and synthetic sensitive text. No real resume, company runbook, credentials or incident data is included.

## 1. Install and run the free demo

Prerequisites: Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). Run commands from the repository root. Installation downloads dependencies; fake-provider execution makes no model or tracing calls.

```bash
cd /Users/anilbhawangirkar/oci-repos/project/opspilot
uv sync --frozen --python 3.12
cp .env.example .env
uv run incidentops demo
```

The demo always uses temporary SQLite storage, fake providers and tracing disabled, even if `.env` selects a live model. Expect a quoted answer for PUB-403 and an abstention for “Is rollback safe to execute now?”. It creates no persistent corpus.

After cloning elsewhere, replace the `cd` path with your clone location. `uv.lock` pins the resolved Python dependencies; use `--frozen` for reproducible installation.

## 2. Ingest and ask questions

Keep the default fake/SQLite configuration initially:

```bash
uv run incidentops ingest
uv run incidentops ask "What does PUB-403 mean in release 2.7?"
uv run incidentops ask "What does PUB-403 mean?" --tenant bravo
uv run incidentops ask "What is the rollback prerequisite for release 2.7?"
uv run incidentops ask "What is the CEO's favourite sandwich?"
```

`ingest` atomically replaces the entire fixture corpus. Repeating it preserves chunk IDs. Editing source text requires a new `version`; changing its ACL takes effect on reingestion. The CLI is a trusted local operator tool and can select tenants. Remote callers use authenticated API tokens.

Output contains `status`, `reason`, `mode`, quoted `claims`, versioned citations, retrieved source references and timing/context metrics. `answered` means the provider selected quotes that passed textual validation. It does **not** establish source truth, relevance, completeness, causality or live rollback safety.

## 3. Start the API

```bash
uv run incidentops serve
```

In a second terminal:

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/v1/ask \
  -H 'Authorization: Bearer acme-demo-token' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does PUB-403 mean in release 2.7?"}'
```

Open [interactive API documentation](http://127.0.0.1:8000/docs), select **Authorize**, enter `acme-demo-token`, and try `/v1/ask`. Inspect a citation by calling its `source_url` with the same bearer token. Bravo's token is `bravo-demo-token`; a cross-tenant source lookup returns 404. The question body cannot choose a tenant or override document permissions.

These are local fixture credentials. Change both tokens in `.env` before shared use. Authentication is a small configured-token adapter; OIDC, rate limits, row-level security and deployment hardening remain future work.

## 4. Run tests and evaluation

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run incidentops evaluate --output results/dev.json
uv run incidentops evaluate --dataset data/eval/holdout.jsonl --output results/holdout.json
```

Evaluation requires ingestion from step 2. Exit code 1 means a failed case; 2 means a command/configuration error. Reports include answerability accuracy, citation integrity, required-document recall and measured local p50/p95 latency. `quality_evidence=false` identifies fake runs. Billed tokens and cost are `null`; a context byte bound is not a billable token count. This small learning dataset is not a benchmark.

Tests exercise ACLs, stale/future sources, versioning, forged quotes, missing evidence, malformed outputs, provider failure, redaction, context limits and trace exports. PostgreSQL tests skip without an explicitly configured disposable database. See [verification results](docs/verification.md).

## 5. Use PostgreSQL and pgvector

Install Docker with Compose support. The database binds to localhost and uses development credentials.

```bash
docker compose up -d db
```

Set this in `.env`, keeping `INCIDENTOPS_PROVIDER=fake`:

```dotenv
INCIDENTOPS_DATABASE_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops
```

```bash
uv run incidentops ingest
uv run incidentops ask "Explain PUB-403"
```

For a complete container path:

```bash
docker compose build api
docker compose run --rm api incidentops ingest
docker compose up -d api
```

Compose connects the API container to `db:5432`. Stop any local API on port 8000 first. Ingestion is explicit: starting the API never silently makes embedding calls.

Test pgvector against a separate database:

```bash
docker compose exec db createdb -U incidentops incidentops_test
INCIDENTOPS_TEST_POSTGRES_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops_test \
  uv run pytest -q -m postgres
```

The test replaces its target corpus: use only the disposable test database. If it already exists, skip `createdb`. The pgvector image version is pinned; Python base images and CI actions are not digest-pinned. Production supply-chain hardening is out of scope.

## 6. Opt in to a live model

API calls cost money. Edit `.env`:

```dotenv
INCIDENTOPS_PROVIDER=openai
OPENAI_API_KEY=your-local-api-key
INCIDENTOPS_CHAT_MODEL=gpt-4.1-mini
INCIDENTOPS_EMBEDDING_MODEL=text-embedding-3-small
INCIDENTOPS_EMBEDDING_DIMENSIONS=1536
INCIDENTOPS_DATABASE_URL=sqlite:///.local/openai.db
```

Use a **new database** for each embedding space. The SQLite URL above is a simple live-provider lab. For pgvector, create `incidentops_live` with `docker compose exec db createdb -U incidentops incidentops_live`, then use `postgresql://incidentops:incidentops@localhost:5432/incidentops_live` in `.env`.

```bash
uv run incidentops ingest
uv run incidentops ask "What does PUB-403 mean in release 2.7?"
uv run incidentops evaluate --output results/live-dev.json
```

There is no silent fake-provider fallback. Outages return `provider_error` or `retrieval_error` and fail evaluation. If your account lacks the configured model, select an available model supporting structured output and record it with your experiment. These defaults are starting configurations, not a best-model recommendation.

Inside Compose, the database URL always targets `incidentops`. For a new embedding space, use a separate project/volume, e.g. `docker compose -p incidentops-live run --rm api incidentops ingest`, after stopping the old project's services to free ports. Never reuse the fake embedding index with a live model.

## 7. Opt in to LangSmith

Set `INCIDENTOPS_TRACING=true`, `LANGSMITH_API_KEY=your-local-key` and optionally `INCIDENTOPS_TRACE_PROJECT=incidentops-increment-1` in `.env`. Restart the API or run another CLI question. Tracing works with either provider.

Find the response `run_id` in the configured project. The application exports status, counts and retrieval/generation/validation durations. It exports **no question, quote, source text, tenant, bearer token or exception payload**. Automatic nested tracing is disabled explicitly to keep raw graph state out of telemetry. This limits prompt-level debugging in exchange for a smaller privacy surface. See [tracing design](docs/low-level-design.md#tracing-and-sensitive-data).

## Understand and explain it

- [High-level architecture](docs/architecture.md): diagrams, boundaries and scaling tradeoffs.
- [Low-level design](docs/low-level-design.md): schemas, SQL, graph nodes and failure behavior.
- [Learning lab](docs/guides/learning-lab.md): code reading order, failure injection and interview Q&A.
- [Architecture decisions](docs/adr/0001-grounded-baseline.md): rationale and alternatives.
- [Roadmap](docs/roadmap.md): all remaining requested topics.
- [Troubleshooting](docs/guides/troubleshooting.md) and [verification record](docs/verification.md).

Stop after increment 1: run it, inspect every citation, break the controls, and explain the tradeoffs before adding hybrid retrieval or operational agents.
