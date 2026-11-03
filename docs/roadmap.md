# Seven increments, with explicit boundaries

Only increment 1 is implemented. The eventual product is a multi-tenant incident investigator with read-only diagnostics and approval-controlled actions against a separate simulator. Do not infer implemented guarantees from this roadmap.

## Increment 1 — Grounded baseline: implemented

Typed source corpus, dense retrieval, two tenants, basic document ACLs/date/status filters, quote citations, abstention, LangChain provider interfaces, a small LangGraph and metadata-only redacted LangSmith export. CLI, FastAPI, SQLite test adapter, PostgreSQL/pgvector adapter, deterministic fake tests and opt-in OpenAI path.

Acceptance: inspect quoted evidence, reject invalid grounding, run offline evaluation, identify fake results as plumbing-only. Actual verification is in [verification.md](verification.md).

## Increment 2 — Retrieval laboratory: planned

Compare fixed-token and structure-aware chunks, table/header preservation, parent expansion and embedding models. Add BM25 over the already-authorized corpus, dense/sparse reciprocal rank fusion, a reranker, version/release metadata selection and one bounded query rewrite.

BM25 is a lexical relevance algorithm; SQL full-text ranking is not automatically BM25. RRF combines ranked candidate lists without pretending their raw scores are calibrated. Reranking scores a small candidate set more deeply, adding latency and model dependence.

Failure exercise: an exact error identifier and a compatibility prerequisite require different evidence units. Gate on required evidence coverage at fixed candidate/context budgets; compare dense-only, hybrid, reranked and rewritten variants with generator held fixed. Measure embedding choices in separate experiments. Add ADR 0002 and exact experiment commands before implementation.

## Increment 3 — Trust and freshness: planned

Expand source supersession/applicability, permission revocation, adversarial retrieval, injection, PII and corrupted-document tests. Introduce independent policy versioning and a tested redacted prompt-inspection workflow. Preserve the current principle that knowledge is data, not instructions.

Failure exercise: a malicious retrieved note requests permission bypass; a stale runbook recommends a worker restart; another tenant's near-identical incident ranks highly. Gate on authored isolation/security tests and explicit known limits. Current basic ACL/redaction tests are groundwork, not completion of this increment.

## Increment 4 — Stateful investigation: planned

Add a separate simulator API serving timestamped deployment, registry-error, worker-health and migration observations. Add a modular background worker. Route documentation questions versus read-only investigations. Run independent diagnostics in parallel. Merge evidence through deterministic ID-based reducers with conflict handling. Separate facts, ranked hypotheses, contradictions and gaps in structured assessments.

Add conversational follow-ups and short-term incident memory. Preserve evidence provenance during summaries. Add explicit graph-step, tool-call, token, elapsed-time and cost budgets; detect repeated calls without new information. Add LangGraph state/reducers, conditional edges and bounded parallel execution with measured tool failures.

Failure exercise: migration API times out while deployment/log tools succeed. Gate on an assessment that describes uncertainty, does not equate correlation with cause and terminates within its configured budget.

## Increment 5 — Durable recovery: planned

Add PostgreSQL checkpoints, authorized thread resume, human-in-the-loop interrupts and an immutable plan record. Approval binds exact plan hash, tenant, actor, resource revision and expiry. Reauthorize and revalidate prerequisites on execution.

Add an action ledger plus **executor-side atomic deduplication** and outcome reconciliation. Only the simulator exposes write operations. A checkpoint records workflow progress; it cannot guarantee exactly-once external effects.

Failure exercise: simulator rollback succeeds, worker crashes before recording completion, process restarts, duplicate resume arrives. Gate on one business effect, recoverable outcome lookup and explicit uncertainty where reconciliation fails. Add crash tests across both sides of the network boundary.

## Increment 6 — Performance and resilience: planned

Add SSE progress streaming while withholding unsupported final claims. Add Redis only here for scoped caches keyed by tenant, principal/policy revision, corpus snapshot and model/prompt configuration. Test revocation invalidation. Add bounded retries/backoff, evaluated fallbacks, circuit breakers, admission limits and cancellation.

Add reviewed long-term memory with provenance, ownership, expiry, correction and deletion. Never promote a generated hypothesis into authoritative knowledge automatically.

Measure latency bottlenecks, concurrency, provider tokens/cost and cache effectiveness under an explicit workload. Failure exercises cover provider failure, cache revocation and dependency outage. Gate on isolation during degradation and measured results; do not invent SLOs based on a tiny local run.

## Increment 7 — Evaluation and monitoring: planned

Build independently labelled development and held-out incident families with required evidence units, forbidden actions and expected outcomes. Add claim-level semantic support evaluation, hallucination detection, LLM-as-judge evaluation calibrated against human labels, prompt regression tests and experiment comparison in LangSmith.

A judge is a fallible evaluator, not a security boundary. Track judge disagreement and regressions separately from deterministic authorization/action tests. Add sampled production-style monitoring for abstention drift, retrieval coverage, latency, failures, tokens and cost under a documented privacy/retention policy.

Failure exercise: a prompt improves normal questions while worsening unsupported answers. Gate on regression detection before promotion, plus measured recovery and action safety. This is when larger datasets and service-level release criteria become meaningful.

## Learning contract for every future increment

Before building, write a bounded design, diagram and pseudocode; make a new ADR; add failing behavior tests. Then implement, rerun checks, record actual traces/measurements, describe alternatives and scale limits, and add an interview exercise. Stop at each boundary so the learner can run, break and explain the system.

The final architecture includes workers, a simulator, BM25 and Redis because their specific stages need them. Empty scaffolds for those components would not demonstrate the requested behavior in increment 1.
