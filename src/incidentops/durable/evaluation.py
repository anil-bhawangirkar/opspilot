import hashlib
import math
from pathlib import Path
from uuid import uuid4

from incidentops.models import Principal, Question


async def evaluate_workflows(runtime, dataset: Path):
    """Local operator experiment; public API remains queue/poll, not inline inference."""
    import json

    cases = []
    latencies = []
    run = str(uuid4())
    required = covered = 0
    for line in dataset.read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        actor = Principal(case["tenant_id"], f"{case['tenant_id']}-demo-operator", ("operators",))
        request = Question(
            question=case["question"],
            region=case.get("region", "west"),
            service=case.get("service", "image-publisher"),
            release=case.get("release", "2.7"),
        )
        job = await runtime.submit_question(actor, f"eval:{run}:{case['id']}", request)
        await runtime.run_job(job["id"])
        view = await runtime.job_view(actor, job["id"])
        result = (view["result"] or {}).get("assessment", {})
        citations = result.get("retrieved", [])
        returned = {c["document_id"] for c in citations}
        required_docs = set(case["required_documents"])
        required += len(required_docs)
        covered += len(required_docs & returned)
        text = "\n".join(c["quote"] for c in result.get("claims", []))
        answerability = (result.get("status") == "answered") == case["answerable"]
        content = all(part in text for part in case["required_quotes"])
        forbidden = all(part not in json.dumps(view) for part in case["forbidden_text"])
        operational = view["status"] == "succeeded" and result.get("reason") not in {
            "invalid_grounding",
            "provider_error",
            "retrieval_error",
            "invalid_output",
        }
        passed = (
            answerability and content and forbidden and operational and required_docs <= returned
        )
        cases.append(
            {
                "id": case["id"],
                "job_id": job["id"],
                "passed": passed,
                "status": result.get("status"),
                "reason": result.get("reason"),
                "operational_ok": operational,
                "answerability_correct": answerability,
            }
        )
        if result:
            latencies.append(result["metrics"]["latency_ms"])
    if not cases:
        raise ValueError("Empty dataset")
    ordered = sorted(latencies)

    def percentile(p):
        return ordered[max(0, math.ceil(len(ordered) * p) - 1)] if ordered else None

    settings = runtime.settings
    return {
        "provider": settings.provider,
        "quality_evidence": settings.provider != "fake",
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "all_passed": all(c["passed"] for c in cases),
        "answerability_accuracy": sum(c["answerability_correct"] for c in cases) / len(cases),
        "required_document_recall": covered / required if required else None,
        "retrieval": {
            k: getattr(settings, k)
            for k in ["dense_k", "lexical_k", "rerank_k", "final_k", "rrf_constant"]
        },
        "vector_backend": settings.vector_backend,
        "hnsw": settings.hnsw,
        "ef_search": settings.hnsw_ef_search,
        "exact_reference": settings.vector_exact,
        "embedding_id": runtime.embedding_id,
        "reranker": "disabled",
        "latency_ms": {"p50": percentile(0.5), "p95": percentile(0.95)},
        "billed_tokens": None,
        "cost_usd": None,
        "cases": cases,
    }
