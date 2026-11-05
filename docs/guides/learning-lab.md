# Increment 1 learning lab

This is the retained **v1** exercise. Its setup/API commands are in the [legacy guide](legacy-v1.md); the current background workflow exercises are in the [v2 guide](production-s2-s3.md).

Run every command from the repository root. Use fake mode and synthetic data until you can explain the entire request path. The goal is understanding and measurement, not memorizing framework names.

## 1. Understand the product decision

Read the current and superseded Acme runbooks under `data/corpus/`, then Bravo's runbook. Ask:

```bash
uv run incidentops demo
uv run incidentops ingest
uv run incidentops ask "Explain PUB-403"
uv run incidentops ask "Explain PUB-403" --tenant bravo
```

Expected: Acme returns a missing publish-permission excerpt; Bravo returns a wrong-namespace excerpt. The error identifier alone is insufficient to choose a mitigation. Tenant identity changes the permitted evidence set.

Explain aloud: “The document describes one possible failure mode. It does not prove that this deployment caused this incident.”

## 2. Follow one request through code

Read these files in order:

1. `src/incidentops/models.py`: distinguish a trusted principal, an untrusted draft and a released assessment.
2. `src/incidentops/api.py`: find where the token becomes tenant identity; note that extra body fields are forbidden.
3. `src/incidentops/pipeline.py`: follow retrieve, conditional edge, generate and validate. Identify each possible abstention reason.
4. `src/incidentops/store.py`: find the WHERE predicates before distance ranking.
5. `src/incidentops/providers.py`: inspect the exact prompt and the fake selector's limited intent rules.

Draw the graph from memory. Explain why no reducer, checkpoint or memory store is needed in this increment: there is no concurrent state merge or persistent conversation yet.

## 3. Inspect citations independently

Start `uv run incidentops serve`, then use the API example in the [legacy guide](legacy-v1.md#api-example). Copy a `source_url` and fetch it using the same bearer token.

Check that the quote is present, the version is current, the content hash matches, and offsets refer to redacted text. Fetch the same source with Bravo's token and expect 404.

```bash
uv run pytest -q tests/test_api.py tests/test_store.py
```

Why it matters: a valid-looking URL is not evidence that the claim came from the document. This implementation builds URLs in code and separately validates the exact quote. It still does not prove the quote is relevant or complete.

## 4. Reproduce the chunking lesson

```bash
uv run pytest -q tests/test_chunking.py
```

The regression test checks that the release heading stays with the PUB-403 explanation. The early custom baseline split paragraphs independently, creating a heading-only retrieval failure. Production step S1 now uses LangChain's recursive splitter and retains the user-visible regression test.

Inspect `chunk_document()` as a compatibility facade, then `RecursiveDocumentChunker` in `chunking.py`. LangChain owns splitting/merging; our adapter owns redaction and provenance. Size and overlap are characters, not actual model tokens. Why not use larger chunks everywhere? Larger chunks can waste context, but too-small chunks lose prerequisites. Compare strategies using required evidence units rather than preference; see the new [S1 guide](production-s1.md).

## 5. Inject failure and inspect the result

These tests deliberately inject a fault and assert the safe result. Passing them means the fault was handled, not that it never occurred.

| Exercise | Command | Expected observation | Production lesson |
|---|---|---|---|
| Fabricated quote with real citation | `uv run pytest -q tests/test_pipeline.py -k forged_quote` | Abstention, `invalid_grounding`, no claims | Citations need claim support checks |
| Invented evidence ID | `uv run pytest -q tests/test_pipeline.py -k unknown_citation` | Entire response rejected | A model cannot mint authority |
| Generation timeout | `uv run pytest -q tests/test_pipeline.py -k provider_failure` | `provider_error`; raw error text withheld | Failure must not look like a successful fallback |
| Malformed model JSON | `uv run pytest -q tests/test_pipeline.py -k malformed` | `invalid_output` | Schema validation is independent of prompt wording |
| Tiny context budget | `uv run pytest -q tests/test_pipeline.py -k context_budget` | Context bound respected; abstention is allowed | Resource limits can reduce evidence coverage |
| Wrong metadata region | `uv run incidentops ask "Explain PUB-403" --region moon` | `no_evidence` | Scope is not silently broadened |
| Source mutation without version bump | `uv run pytest -q tests/test_store.py -k reused_document` | Reingestion rejected | Stable citations require version discipline |
| Access revoked during generation | `uv run pytest -q tests/test_boundaries.py -k revoked` | No answer or retrieval metadata released | Recheck policy at release, including metadata |
| Source update transaction fails | `uv run pytest -q tests/test_boundaries.py -k failed_reingestion` | Prior corpus still answers | Partial index replacement creates correctness failures |
| Trace backend unavailable | `uv run pytest -q tests/test_tracing.py -k failure` | Answer still available | Telemetry should not become a serving dependency |

For a manual experiment, copy the corpus to `.local/lab-corpus`, set `INCIDENTOPS_CORPUS_DIR=.local/lab-corpus` and a fresh SQLite database URL in `.env`, then edit a runbook and ingest. Bump its version before intentionally changing content. Restore the corpus path/database URL afterward. Do not edit the baseline fixtures while collecting a comparison.

## 6. Understand the redaction boundary

```bash
uv run pytest -q tests/test_pipeline.py -k redaction
uv run pytest -q tests/test_tracing.py
```

Trace tests use a capturing client to inspect exactly what would cross the export boundary; they do not call LangSmith. The end-to-end provider test checks that automatic tracing is disabled and the generator sees a redacted question.

Explain the limitation: regular expressions cover known synthetic patterns, not all PII. Hashing a credential does not make its original content safe. Disabling prompt export also sacrifices debugging detail; data classification and an approved redacted-inspection mode are later design work.

## 7. Measure without fooling yourself

```bash
uv run incidentops evaluate --output results/dev.json
uv run incidentops evaluate --dataset data/eval/holdout.jsonl --output results/holdout.json
```

Open the reports and identify:

- Provider, embedding identity, prompt version and dataset/corpus hashes.
- Whether unanswerable cases abstained and answerable cases were unnecessarily rejected.
- Whether required documents were retrieved and reference answer phrases appeared.
- Whether runtime failures were mistakenly rewarded as abstentions. They must fail the case.
- What the p95 means with only eight development cases: one of a few local measurements, not a useful capacity estimate.

Set `INCIDENTOPS_TOP_K=1` in `.env`, rerun evaluation to another file, and inspect any change. Restore the setting. A fake result cannot choose a production embedding model. A larger k can improve coverage while increasing irrelevant context, latency and cost.

The file named `holdout` demonstrates incident-family separation, but it is visible and tiny. For meaningful model selection, write independent cases before tuning, split by source/incident family, keep a final set untouched and have another reviewer label evidence.

## 8. Move to real components independently

First run fake providers against real pgvector using the [legacy setup steps](legacy-v1.md#postgresql-and-containers). This isolates storage integration from model behavior. Then use a separate index with a live provider. Run exactly the same development cases and record actual failures, not just overall accuracy.

Enable LangSmith only after the local privacy tests pass. Inspect one run UUID and verify that only allowlisted fields appear. A test capture proves our export payload shape; only an actual service call proves credentials, permissions and remote ingestion work.

## Interview questions and model answers

**Why this use case?** “It connects to my cloud operations experience and exposes important AI failure modes: plausible but cross-tenant evidence, stale runbooks, missing prerequisites and uncertainty about live state. The project is an experimental testbed; I describe production experience separately.”

**Why not start with a general agent?** “Before granting tools or side effects I wanted a measurable evidence contract. The first workflow retrieves documentation and validates support. Later agent stages can reuse it without weakening authorization.”

**Does citation validation eliminate hallucination?** “No. I verify that an excerpt exists in the authorized version used for this request. A true excerpt can be irrelevant or omit a caveat. Semantic support, completeness and source truth need additional evaluation.”

**Why exact search before HNSW?** “The corpus is tiny. Exact search is simple and supplies a reference to measure approximate-neighbor recall later. ANN quality is distinct from whether the retrieved evidence answers the question, especially under selective metadata filters.”

**Why direct SQL rather than a vector-store wrapper?** “I wanted the tenant and access predicates visible and testable before ranking. LangChain still provides embeddings, prompts and structured model integration. A wrapper is acceptable if its filtering and authorization semantics are equally explicit.”

**How do you prevent tenant leakage?** “Trusted token configuration resolves identity outside model-editable state. SQL restricts tenant and document group before ranking; source lookup and response release repeat authorization checks. I have tests for known failure paths, not a universal isolation proof.”

**How do you handle missing evidence?** “The graph bypasses generation if no evidence fits. The provider may also abstain; malformed schemas, invalid quotes and dependency errors produce distinct abstention reasons. Evaluation penalizes false abstentions and outages separately.”

**Why both fake and live providers?** “Fake mode makes policy and failure tests deterministic and free. It cannot evaluate reasoning or semantic retrieval. A live run is separately labelled and needs independent reference cases and actual measurements.”

**What does LangGraph guarantee here?** “Explicit state transitions and a bounded acyclic flow. This increment does not persist state or run external actions. Checkpointing, when added, still cannot provide exactly-once external effects.”

**How will you make approved recovery safe?** “In increment 5, bind approval to the exact plan hash, actor, resource revision and expiry. Reauthorize on resume. A downstream simulator must atomically deduplicate action keys and expose outcome lookup for uncertain results. A graph checkpoint alone is insufficient.”

**How do you choose chunk size?** “By measured evidence coverage under a fixed context budget, including exceptions and prerequisites. I already saw heading-only chunks crowd out answer paragraphs; that justified one concrete correction. Broader strategy comparison is the next increment.”

**What would change for production?** “OIDC, least-privilege roles, row-level security, schema migrations, retention, stronger PII controls, connection pooling, admission control, deadlines, a representative evaluation set and service-level monitoring. I would measure the workload before claiming scale or cost targets.”

**What are your actual results?** Read `docs/verification.md` and your local reports. Quote the measured test counts and configuration. State which services were not exercised. Never translate a scripted fixture score into an LLM quality claim.

## Ready to leave increment 1?

You should be able to explain one successful answer, one appropriate abstention, one unnecessary abstention, the exact permission predicates, the quotation validator's limits and every metric in the report. Only then proceed to increment 2's retrieval experiments.
