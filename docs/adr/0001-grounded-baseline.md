# ADR 0001: A small, inspectable grounded baseline

**Status:** Accepted for increment 1. **Date:** 2026-11-03.

## Context

The project is a production-style learning POC, not an operational deployment. The learner needs to explain why a RAG answer is supported, why another tenant's evidence is inaccessible and why uncertainty can be a valid outcome. The supplied brief explicitly asks to stop after the first increment.

## Decisions and consequences

| Decision | Why | Alternative | Tradeoff / revisit trigger |
|---|---|---|---|
| Extractive answer contract | Code can verify exact textual support without claiming semantic judgment | Free-form generation plus citation IDs | Less natural answers; add paraphrases only with claim-support evaluation and human calibration |
| Separate untrusted `Draft` from final `Assessment` | Schema-valid model output still needs policy and evidence checks | Return model JSON directly | More validation code, but visible trust boundary |
| Scope in SQL before ranking | Similarity must never determine access | Retrieve globally and filter results afterward | Explicit SQL is more maintenance; post-filtering can expose evidence or starve recall |
| Principal outside graph state | Models cannot rewrite tenant identity | Put tenant in prompt or mutable state | Requires trusted runtime wiring rather than a generic chat-state dictionary |
| Version and hash every source chunk | Reproducible inspection; detect in-place edits | Mutable IDs pointing to latest content | Requires version discipline and a later archive strategy |
| Atomic full-corpus replacement | Tiny corpus, simple correctness and revocation semantics | Incremental ingestion and embedding cache | Re-embeds unchanged content; add incremental updates after a measured need |
| Group adjacent paragraphs | Avoid heading-only retrieval failures | Fixed character windows | Not sufficient for large tables or code; compare strategies in increment 2 |
| Exact pgvector cosine search | Simple baseline and reference for future ANN comparisons | HNSW from the start | Linear vector search is unsuitable for large permitted corpora; benchmark before switching |
| Direct psycopg adapter | Authorization predicates are easy to audit | LangChain vector-store wrapper | More adapter code; LangChain still supplies embeddings, prompts and models |
| SQLite/fake offline path | Free, deterministic, isolated tests | Require Docker and paid calls for all tests | Must explicitly avoid conflating plumbing results with semantic quality |
| Small acyclic LangGraph | Make branching and state visible before durable agents | Ordinary Python orchestration | Adds framework overhead for a small flow; prepares a clear seam for future states |
| Metadata-only application traces | Avoid leaking source material through automatic state serialization | Full prompt/state traces with regex masking | Less remote debugging detail; later redacted inspection needs a deliberate privacy design |
| No agent actions, worker or simulator yet | Respect the first-increment boundary | Scaffold the entire platform immediately | Cannot demonstrate action idempotency yet; later tests must prove it independently |

## Considered architecture alternatives

1. A notebook-only demo would minimize setup but hide API authentication, persistent citation lookup and operational failure contracts.
2. The selected modular package supports API, CLI and tests with the same business logic and optional infrastructure.
3. A complete distributed agent service with queues, Redis and recovery tools would fit the eventual architecture but make it harder to learn the first grounding guarantees and verify their limits.

## Guarantees and limits

The tests establish authored behavior for fixture ACLs, stale sources, redaction patterns and validation failures. They do not establish comprehensive security, universal hallucination detection or model quality. A real quote can still be misleading; the source can still be wrong. At scale, revocation consistency, concurrency and privacy need independent designs and evaluation.

## Reversal strategy

Provider implementations can change behind the embedding/generator contracts. Retrieval can add BM25/RRF and reranking while retaining the same SQL permission boundary. A new embedding space requires a new database and reingestion. Future stateful graphs can wrap this RAG stage while supplying identity through trusted runtime context. Do not reinterpret today's fake scores as a baseline for real-model quality.
