# Legacy v1: synchronous RAG baseline

V1 remains a runnable teaching comparison. The main project path is now [v2 durable workflows](production-s2-s3.md). V1 and v2 have separate source stores and CLI entry points; ingesting one does not populate the other.

## Free local setup

Run from the repository root with Python 3.12–3.14 and uv installed:

```bash
uv sync --frozen
uv run incidentops demo
```

The demo uses temporary SQLite, fake providers and tracing disabled. It ignores live model/database settings and leaves no persistent corpus. For persistent v1 work, copy `.env.example` to `.env` only if it does not already exist, and keep `INCIDENTOPS_PROVIDER=fake`:

```bash
uv run incidentops ingest
uv run incidentops ask "What does PUB-403 mean in release 2.7?"
uv run incidentops ask "Explain PUB-403" --tenant bravo
uv run incidentops ask "Is rollback safe to execute now?"
uv run incidentops serve
```

`ingest` is a trusted operator command that replaces the entire fixture snapshot. It must not be used as a multi-tenant upload service. Chunk IDs are stable within a splitter profile; edited source text needs a new version. The S1 splitter settings use characters, not model tokens.

## API example

In another terminal:

```bash
curl -s http://127.0.0.1:8000/v1/ask \
  -H 'Authorization: Bearer acme-demo-token' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does PUB-403 mean in release 2.7?"}'
```

Open `http://127.0.0.1:8000/docs`, authorize with the same token, and inspect the response citation's `source_url`. Bravo uses `bravo-demo-token`; cross-tenant source lookups return 404. These are synthetic local credentials; configure different tokens for shared use.

The v1 graph in `src/incidentops/pipeline.py` follows retrieve/pack → conditional generation → quote/access validation. It is acyclic, compiled per request and has no persistent checkpoints or background job ledger. `services/ingestion.py`, `services/retrieval.py`, `ports.py`, `store.py`, `factory.py` and the top-level `api.py`, `cli.py`, `evaluation.py` remain used by this path. Shared models, providers, redaction, chunking and tracing also support v2.

## Evaluation

After ingestion:

```bash
uv run incidentops evaluate --output results/dev.json
uv run incidentops evaluate --dataset data/eval/holdout.jsonl --output results/holdout.json
uv run pytest -q tests/test_pipeline.py tests/test_api.py tests/test_store.py
```

Fake results are mechanics checks. The scripted selector was authored with the tiny visible datasets; it does not establish semantic quality. Exit code 1 indicates failed evaluation cases; 2 indicates configuration/command failure.

## PostgreSQL and containers

```bash
docker compose up -d db
```

Set `INCIDENTOPS_DATABASE_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops` in `.env`, then rerun `incidentops ingest`. The complete v1 container path is:

```bash
docker compose build api
docker compose run --rm api incidentops ingest
docker compose up -d api
```

The v1 API binds localhost port 8000; stop a host API using that port first. Ingestion is explicit. The optional integration tests use a disposable database:

```bash
docker compose exec db createdb -U incidentops incidentops_test
INCIDENTOPS_TEST_POSTGRES_URL=postgresql://incidentops:incidentops@localhost:5432/incidentops_test \
  uv run pytest -q -m postgres
```

Skip `createdb` if that test database already exists. These tests modify their target database.

## Optional live provider and tracing

For a deliberately configured paid experiment, set `INCIDENTOPS_PROVIDER=openai`, `OPENAI_API_KEY` and a model supporting structured output in `.env`. The checked-in starting settings are `INCIDENTOPS_CHAT_MODEL=gpt-4.1-mini`, `INCIDENTOPS_EMBEDDING_MODEL=text-embedding-3-small` and `INCIDENTOPS_EMBEDDING_DIMENSIONS=1536`; account availability must be checked locally. Use a new database/index for another embedding space, for example `INCIDENTOPS_DATABASE_URL=sqlite:///.local/openai.db`, then ingest and evaluate again. There is no silent fake fallback.

Optional tracing uses `INCIDENTOPS_TRACING=true`, `LANGSMITH_API_KEY` and `INCIDENTOPS_TRACE_PROJECT`. It exports allowlisted measurements under an opaque run ID; raw prompts, source text, quotes, identity and exception payloads are excluded. See [tracing and sensitive data](../low-level-design.md#tracing-and-sensitive-data).

Read the [v1 learning lab](learning-lab.md), [S1 library migration](production-s1.md), [original ADR](../adr/0001-grounded-baseline.md) and historical [baseline verification](../verification.md) / [S1 verification](../verification-s1.md) for the evolution. The original [design](../plans/incidentops/2026-11-03-incidentops-design.md) and [completed implementation plan](../plans/incidentops/2026-11-03-incidentops.md) are retained historical records. The current diagrams and design are in [architecture](../architecture.md) and [low-level design](../low-level-design.md).
