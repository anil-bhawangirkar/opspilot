import json
import subprocess
import sys

import pytest

from incidentops.durable.cli import enqueue_fixtures


async def test_local_seeding_fails_if_a_persisted_ingestion_did_not_succeed(runtime):
    async def fail_embeddings(texts):
        raise ValueError("Synthetic invalid embedding request")

    runtime.embeddings.aembed_documents = fail_embeddings
    with pytest.raises(RuntimeError, match="Fixture ingestion did not succeed"):
        await enqueue_fixtures(runtime, execute=True)


def test_offline_workflow_demo_uses_real_migration_and_durable_results():
    result = subprocess.run(
        [sys.executable, "-m", "incidentops.durable.cli", "demo"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["quality_evidence"] is False
    assert body["ingestion_jobs_succeeded"] >= 4
    assert body["answer_status"] == "answered"
    assert body["duplicate_worker_ran"] is False
