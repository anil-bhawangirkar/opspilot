# Execution Plans

Use a living Execution Plan for significant research, multi-stage production changes or large refactors; follow `PLANS.md`. Keep implemented behavior distinct from proposed architecture. Current plan: `docs/plans/production-evolution/2026-11-05-execplan.md`.

Work in the current checkout unless the user requests another location. Do not alter unrelated `.idea/` files. Keep the free deterministic provider path. Never make paid provider calls or deploy cloud infrastructure merely to validate a change. Write behavior tests before implementation and verify citation provenance, tenant scope and redaction when changing retrieval or ingestion.

After migration 0001 is delivered, keep `durable/schema_v1.py` frozen. Add later Alembic revisions and current schema changes explicitly; do not rewrite old migration behavior by importing mutable application metadata.
