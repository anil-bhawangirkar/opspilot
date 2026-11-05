from pathlib import Path

import pytest

from incidentops.durable.cli import enqueue_fixtures
from incidentops.durable.evaluation import evaluate_workflows


@pytest.mark.asyncio
async def test_hybrid_workflow_evaluation_is_explicitly_fake(runtime):
    await enqueue_fixtures(runtime, execute=True)
    result = await evaluate_workflows(runtime, Path("data/eval/dev.jsonl"))
    assert result["case_count"] == 8
    assert result["all_passed"]
    assert result["quality_evidence"] is False
    assert result["retrieval"]["rrf_constant"] == 60
    assert result["reranker"] == "disabled"
    assert result["dataset_sha256"]
