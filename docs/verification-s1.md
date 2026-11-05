# Production foundation S1 verification

Historical S1 evidence. See [v2 verification](verification-v2.md) for the later durable job/retrieval foundation; limitations below describe S1 at its original boundary.

Date: 2026-11-05. This is the result of the library/module refactor, not completion of the distributed target architecture. Original baseline evidence remains in `verification.md`.

## Actual checks

| Check | Result |
|---|---|
| Baseline before edits: `.venv/bin/python -m pytest -q` | 39 passed, 1 PostgreSQL test skipped |
| New splitter/ingestion tests before implementation | Failed because requested modules/factories did not exist |
| Empty-input/config regression tests before fixes | Four failures: empty ingestion could clear the snapshot, whitespace input accepted, alias prevented field-name configuration and therefore overlap validation |
| Full suite after fixes: `.venv/bin/python -m pytest -q` | **61 passed, 1 PostgreSQL test skipped** |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed |
| `git diff --check` | Passed |
| `uv sync --frozen --offline` | Passed after downloading and locking the splitter dependency |
| `uv build --offline --out-dir /private/tmp/incidentops-s1-dist` | Source distribution and wheel built |
| Isolated `incidentops demo` | Quoted PUB-403 answer and abstention on live rollback safety |
| Fake ingestion into `.local/s1.db` | 11 chunks using character-size 350, target overlap 40 |
| Development fixture evaluation | 8/8 passed, `quality_evidence=false` |
| Holdout-layout fixture evaluation | 2/2 passed, `quality_evidence=false` |

Locked splitter version: `langchain-text-splitters==1.1.3`. Inspected its installed `create_documents()` and recursive split implementation before wiring offsets. No custom implementation of recursive splitting remains.

## Local measurements

One pass on macOS/Apple Silicon, Python 3.12.13, fake hashed embeddings, scripted selector, SQLite exact search, top-k 5 and evidence byte bound 1,800. No concurrent workload or remote service call.

| Dataset | p50 | p95 | Meaning |
|---|---:|---:|---|
| Eight development cases | 2.941 ms | 5.217 ms | Local plumbing timing only |
| Two holdout-layout cases | 1.936 ms | 4.383 ms | Too few samples for a service-tail estimate |

Actual indexed redacted snapshot SHA-256: `f154c8c670b06fdd9b25247a30cfb586a1cc4676e46a45d74aa1ca83bfa646b4`. Corpus files are unchanged from the baseline, but the profile/IDs/metadata changed. Results are in ignored `results/s1-dev.json` and `results/s1-holdout.json`; the S1 guide gives exact reproduction commands. These scores do not demonstrate retrieval improvement or LLM answer quality.

## Demonstrated behavior

- Redaction occurs before LangChain Document conversion and embedding; tenant/version metadata survives.
- Standard recursive splitting handles tested Unicode/repeated-text offsets, bounded character lengths and target overlap on unbroken text.
- IDs are deterministic within a profile and differ when the profile changes, even if a short chunk's text does not.
- Invalid size/overlap, empty documents, empty ingestion and maximum chunk-count violations fail before publication.
- Each provider call fits the configured batch item count. Incorrect embedding counts and a later batch timeout leave the previous snapshot available.
- Ingestion initializes no chat generator. The graph can use a source reader without ingestion/search methods and an independently supplied retriever.
- Existing authored tenant/ACL, grounding, redaction, source freshness and telemetry tests continue passing.

## Explicitly not verified or implemented

No Docker/PostgreSQL runtime was available, so its integration test remains skipped. No live model or remote LangSmith call, broker, Redis, S3, cloud deployment, distributed crash/replay or throughput test ran. The code does not yet supply durable workers, tenant-scoped publication, global rate limits, fair queues or checkpoints. Bounded batching still stages capped vectors in memory. Global snapshot replacement remains a trusted fixture CLI only.

## Review and next milestone

Controller review checked changed code, the plan against the document-review checklist, current-doc consistency, library source behavior and migration risks. This is not an independent external audit. Kept unrelated `.idea/` files unchanged; no Git commit, push or cloud mutation was performed.

S1 is complete. S2 starts with migrations, tenant-scoped source/index generations, durable job/outbox state and object manifests. Worker deployment follows those correctness foundations; it must not wrap the existing global replacement writer.
