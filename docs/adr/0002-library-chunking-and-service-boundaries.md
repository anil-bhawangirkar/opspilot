# ADR 0002: Library splitting and narrow service boundaries

**Status:** Implemented for production foundation S1. **Date:** 2026-11-05.

## Problem

The user wants production engineering depth and direct use of LangChain components. The previous manual splitter had no measured benefit, mixed standard mechanics with domain provenance and used a misleading token-named byte budget. Ingestion and retrieval were coupled to a concrete store; CLI ingestion initialized a chat generator it did not use.

## Decision

Use LangChain Document and RecursiveCharacterTextSplitter behind `RecursiveDocumentChunker`. Redact before constructing the Document; preserve typed tenant/version/ACL metadata through its metadata dictionary. Use character units consistently with the upstream splitter's start-index algorithm; validate every returned source slice, including repeated Unicode tests. Keep the evidence context's separate UTF-8 byte upper bound unchanged.

Record `recursive-chars-v1:size=...:overlap=...` on each chunk and include it in chunk IDs. Reingest for the new profile. Same source text retains its document hash, but old citation IDs are intentionally not reused across splitter configurations. Reading old stored chunks remains possible through the legacy profile default while their current authorization permits it.

Extract ingestion and retrieval use cases behind narrow structural protocols. An ingestion-specific factory builds embeddings and a writer without a chat model or trace exporter. Bounded provider batches and a maximum chunk count protect this small in-memory staging implementation. Reject empty ingestion instead of erasing a snapshot accidentally; publish only after all batches succeed.

## Alternatives and consequences

- Keep custom splitting: less new dependency, but maintain a second algorithm and its edge cases. Rejected without evidence of advantage.
- Replace domain models with unvalidated dictionaries: convenient framework interchange, but weakens required tenant/version metadata. Keep both representations at the adapter boundary.
- Use token-aware splitting immediately: useful for precise token budgets, but introduces tokenizer configuration/offset/overlap considerations. Compare in the retrieval lab; do not relabel characters as tokens.
- Add a worker queue before changing storage: creates distributed delivery around a global destructive snapshot writer. Rejected; tenant-scoped generation promotion must precede multi-tenant workers.

Target overlap is a heuristic, not a promise of complete facts or exactly equal overlap for every paragraph boundary. This change adds no provider-quality improvement claim, durable execution, cloud deployment or general isolation proof. It is a maintainability and contract improvement with testable behavior.

## Evidence and next step

See the S1 guide for test commands and actual verification. S2 must replace global snapshot writes with migration-managed, tenant-scoped staged generations and object manifests. Celery/RabbitMQ, Redis, S3 and LangGraph checkpoints remain proposed later stages in the target design.
