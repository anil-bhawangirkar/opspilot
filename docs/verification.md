# Verification record — increment 1

Historical v1 evidence. The current implementation and fresh workflow results are described in [v2 verification](verification-v2.md); the old first-increment stopping boundary below is superseded by the [active plan](plans/production-evolution/2026-11-05-execplan.md).

Verified on **2026-11-03** in the requested checkout. Environment: macOS 26.7.1, Apple Silicon, Python 3.12.13, uv 0.12.1. This record distinguishes executed checks from supplied but untested integration paths.

## Executed checks

| Command / check | Actual result |
|---|---|
| `uv sync --frozen --offline` after initial dependency download | Installed environment matches the locked graph |
| `uv run pytest -q` | **39 passed, 1 skipped**; the skipped test requires real PostgreSQL/pgvector |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed |
| `uv run incidentops demo` | Answered PUB-403 with a supported quote; abstained on live rollback safety; fake mode |
| `uv run incidentops ingest` | Eight source files produced 11 deterministic chunks; persisted redacted text |
| `uv run incidentops evaluate --output results/dev.json` | 8/8 authored cases passed in fake/SQLite mode |
| `uv run incidentops evaluate --dataset data/eval/holdout.jsonl --output results/holdout.json` | 2/2 authored cases passed in fake/SQLite mode |
| `uv build --offline --out-dir /private/tmp/incidentops-dist` | Source distribution and wheel built successfully |
| Live-provider adapter construction with a synthetic placeholder key | OpenAI embedding and structured-generation adapters initialized; **zero network/model calls** |
| YAML parser for Compose and CI configuration | Parsed successfully; not a container/runtime test |
| Local HTTP smoke test on port 18001 | Health 200, answer 200, authorized citation 200, cross-tenant citation 404, missing auth 401, Swagger page 200 |

The sandbox initially prevented package downloads and localhost sockets. Dependencies were downloaded and localhost-only HTTP checks were run through the permitted escalation mechanism. The temporary API server was stopped after verification. API tests also run in-process without socket access.

## Local fake evaluation measurements

These are measured one-pass results for the tiny fixture datasets, **not model-quality or production-performance results**. The generator is a scripted selector and the embedding is hashed word counts. There was no concurrent load, network model call or remote trace export.

| Dataset | Cases | Answerability accuracy | Citation integrity | Required-document recall | Local p50 | Local p95 |
|---|---:|---:|---:|---:|---:|---:|
| Development | 8 | 1.0 | 1.0 | 1.0 | 2.388 ms | 4.258 ms |
| Holdout-layout example | 2 | 1.0 | 1.0 | 1.0 | 2.047 ms | 4.508 ms |

Configuration: `fake-hashed-words-v1:256`, `scripted-fixture-v1`, prompt `extractive-v1`, top-k 5, evidence JSON upper bound 1,800 UTF-8 bytes, chunk upper bound 350 bytes, SQLite exact cosine ranking. `quality_evidence=false` in both reports. Token billing and cost are `null`.

Reproduction identifiers:

```text
configured corpus files SHA-256:
fea89f8bc6bad13a281232c90d0a65566f4fda727654493cc49c9817e6216125
actual indexed redacted snapshot SHA-256:
0c29f0e5709d43f3f613033f783b8f9db3ca15f5db6a09dfd02cd6b434054afa
development dataset SHA-256:
e474068942c9b05439fc57535c1234343559adf5df056ed711bb496b51413c57
holdout-layout dataset SHA-256:
6fd4046e29541e41f1b6168e691d5833a3f503f2917c4985d740d732f327818f
```

Reports are regenerated under ignored `results/`. Latency varies by machine, process warmup and system load. Ten cases cannot support a meaningful service p95 or reliability estimate. The visible holdout file is a teaching split, not untouched independent validation.

## Failure evidence and corrections

- An initial answer test abstained because heading-only chunks displaced the answer paragraph from top-k. Retrieval inspection identified the ranked texts. A failing chunk-boundary regression test was added; paragraph packing restored the documented answer without widening authorization.
- A revocation test showed that rejected answer claims were removed while retrieval-reference metadata still remained. A final authorization check now covers both, and the regression passes.
- A configuration test showed standard provider keys in `.env` were not automatically passed to SDK clients. Explicit secret-valued settings now support that setup path without requiring shell exports.
- A non-ASCII token test exposed `compare_digest`'s string restriction. Byte comparisons reject an invalid token without raising a server error.
- A zero-vector test exposed an undefined cosine-search input. The store now rejects it before either backend ranks results.
- Trace export tests originally found zero-length UI spans despite measured stage-duration fields. Span durations now reflect measured intervals. Export captures show only allowlisted fields and a generic failure message when the trace backend fails.

## Not verified in this environment

- **PostgreSQL/pgvector execution and Docker image build:** no local Docker or PostgreSQL executable was available. `tests/test_postgres.py` and a separate CI job are supplied. A YAML parse and SQLite tests are not substitutes for that integration check.
- **Paid model responses and embedding quality:** no live API calls were made. Adapter initialization only checks local SDK compatibility. Real quality remains unmeasured.
- **Remote LangSmith authentication, delivery and UI rendering:** payloads were captured locally in tests; no remote export was attempted.
- **GitHub Actions execution:** workflow files are supplied, not run on GitHub. Nothing was pushed or published.
- **Scale, adversarial security, comprehensive PII protection, checkpoint recovery or action idempotency:** these are not established by increment 1.

## Review outcome and next boundary

Controller review checked the trusted identity boundary, source authorization, quote validation, safe telemetry, setup commands and fake/live distinctions. It led to the corrections above. This was a local review, not an independent external audit.

The first increment is ready for the learner's offline inspection. Leave the current changes in the requested working folder. Stop before implementing hybrid retrieval, live tools, workers, a simulator, memory or recovery actions. Follow the README to run optional integrations, then use the learning lab to inspect, break and explain the baseline.
