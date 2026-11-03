import hashlib
import json
import math
import platform
from datetime import UTC, datetime
from pathlib import Path

from incidentops.models import Principal, Question
from incidentops.pipeline import RAGService
from incidentops.providers import PROMPT_VERSION


def evaluate(service: RAGService, dataset: Path) -> dict:
    rows = []
    required_units = retrieved_units = 0
    latencies = []
    for line in dataset.read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if case["tenant_id"] not in {"acme", "bravo"}:
            raise ValueError("Unknown evaluation tenant")
        principal = Principal(case["tenant_id"], "evaluation-operator", ("operators",))
        result = service.ask(
            principal,
            Question(
                question=case["question"],
                region=case.get("region", "west"),
                service=case.get("service", "image-publisher"),
                release=case.get("release", "2.7"),
            ),
        )
        answerable_ok = (result.status == "answered") == case["answerable"]
        quote_text = "\n".join(claim.quote for claim in result.claims)
        required = set(case["required_documents"])
        retrieved = {c.document_id for c in result.retrieved}
        required_units += len(required)
        retrieved_units += len(required & retrieved)
        integrity = True
        for claim in result.claims:
            chunk = service.store.get_chunk(principal, claim.citation.chunk_id)
            integrity &= bool(
                chunk
                and claim.quote in chunk.text
                and chunk.content_hash == claim.citation.content_hash
            )
        content_ok = all(part in quote_text for part in case["required_quotes"])
        forbidden_ok = all(part not in result.model_dump_json() for part in case["forbidden_text"])
        # Infrastructure failures must fail even a case whose expected result is abstention.
        operational_ok = result.reason not in {
            "provider_error",
            "retrieval_error",
            "invalid_output",
            "invalid_grounding",
        }
        passed = (
            answerable_ok
            and integrity
            and content_ok
            and forbidden_ok
            and required <= retrieved
            and operational_ok
        )
        rows.append(
            {
                "id": case["id"],
                "passed": passed,
                "run_id": result.run_id,
                "status": result.status,
                "reason": result.reason,
                "answerability_correct": answerable_ok,
                "citation_integrity": integrity,
                "reference_substrings_present": content_ok,
                "forbidden_text_absent": forbidden_ok,
                "operational_ok": operational_ok,
            }
        )
        latencies.append(result.metrics.latency_ms)
    if not rows:
        raise ValueError("Evaluation dataset is empty")
    ordered = sorted(latencies)

    def percentile(p):
        return ordered[max(0, math.ceil(len(ordered) * p) - 1)]

    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "provider": service.mode,
        "quality_evidence": service.mode != "fake",
        "quality_note": "Fake fixtures test plumbing only."
        if service.mode == "fake"
        else "Live small synthetic evaluation; not a general reliability estimate.",
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "indexed_corpus_sha256": service.store.snapshot_fingerprint(),
        "embedding_id": service.store.embedding_id,
        "store": "postgresql-pgvector" if service.store.postgres else "sqlite-exact-test-adapter",
        "context_token_upper_bound_budget": service.context_tokens,
        "top_k": service.top_k,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "case_count": len(rows),
        "all_passed": all(row["passed"] for row in rows),
        "answerability_accuracy": sum(r["answerability_correct"] for r in rows) / len(rows),
        "citation_integrity_rate": sum(r["citation_integrity"] for r in rows) / len(rows),
        "required_document_recall": retrieved_units / required_units if required_units else None,
        "latency_ms": {"p50": percentile(0.5), "p95": percentile(0.95)},
        "billed_tokens": None,
        "cost_usd": None,
        "cases": rows,
    }
