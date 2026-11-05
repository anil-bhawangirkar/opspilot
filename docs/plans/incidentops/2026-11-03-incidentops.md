# IncidentOps Implementation Plan

Completed historical v1 plan. The [active production plan](../production-evolution/2026-11-05-execplan.md) tracks current work; this record remains for the original test/decision history.

**Goal:** Deliver the first runnable, documented increment of the supplied IncidentOps proposal.

**Architecture:** A Python package exposes a CLI and FastAPI. A three-node LangGraph composes filtered retrieval, structured generation and deterministic quotation validation. Store and provider protocols allow SQLite/fake tests and PostgreSQL/OpenAI deployment.

**Constraints:** Synthetic data only; no infrastructure actions; no paid calls without explicit local configuration; remain in the current folder; preserve later increment boundaries.

**Acceptance Signals:** Offline demo and evaluation run, test suite passes, lint passes, lockfile resolves, design/setup/learning docs match actual behavior. Optional service checks are honestly distinguished.

## Chunk 1: Runnable grounded baseline

### Task 1: Package, fixtures and policy contracts
Files: `pyproject.toml`, `.env.example`, `data/corpus/`, `src/incidentops/models.py`, `security.py`, `ingestion.py`, `store.py`, `tests/test_store.py`.
1. Add behavior tests for authorized retrieval, source lookup, version filtering and repeat ingestion.
2. Run `uv run pytest tests/test_store.py`; expect missing package/behavior failures.
3. Implement typed documents, stable chunks, redaction and the two store adapters.
4. Rerun targeted tests; expect pass. No database claim without real PostgreSQL evidence.

### Task 2: Provider, graph and grounding
Files: `providers.py`, `pipeline.py`, `tracing.py`, `tests/test_pipeline.py`.
1. Write tests for quoted answers, abstention, forged evidence and provider outage; run targeted tests and observe missing behavior.
2. Implement LangChain fake/live interfaces, bounded retrieval and the acyclic graph.
3. Rerun `uv run pytest tests/test_pipeline.py`; expect pass, including no raw trace export.

### Task 3: API, CLI, evaluation and deployment
Files: `api.py`, `cli.py`, `evaluation.py`, `data/eval/`, `compose.yaml`, `Dockerfile`, `.github/workflows/ci.yml`, `tests/test_api.py`, `tests/test_evaluation.py`.
1. Add endpoint and evaluation contract tests, run them red, implement and run green.
2. Run `uv run incidentops demo`, `uv run incidentops evaluate`, `uv run pytest`, `uv run ruff check .`; expect successful offline outputs and explicit fake labels.
3. Add opt-in PostgreSQL tests, run only with a configured test database. Do not replace this with mocks and claim integration success.

### Task 4: Learning and handoff
Files: `README.md`, `docs/architecture.md`, `docs/low-level-design.md`, `docs/guides/learning-lab.md`, `docs/adr/`, `docs/roadmap.md`, `docs/verification.md`.
Write step-by-step commands, diagrams, rationale, alternatives, failure exercises and interview answers. Check local links, rerun documented commands and record actual results and unavailable integrations. No push, publication or commit is required by the user's request.

## Review

Controller review: approved. Each task has paths and verification criteria. Execute the supplied first-increment scope without introducing agent execution or unsupported quality claims.

## Execution result

All four tasks completed for increment 1. The offline demo, CLI ingestion/evaluation, authenticated HTTP smoke checks, lint, formatting and package build ran successfully. Final behavioral suite: 39 passed, one PostgreSQL integration test skipped. PostgreSQL/Docker, live-model calls and remote LangSmith delivery remain explicitly unverified. No later increment, Git publication or infrastructure action was performed. See `docs/verification.md` for measurements, failure evidence and limits.
