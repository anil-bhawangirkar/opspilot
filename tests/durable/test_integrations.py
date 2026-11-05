"""Opt-in real service checks. Never substitute a stub result for these tests."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from incidentops.durable.broker import CeleryPublisher, create_celery_app, dispatch_once
from incidentops.durable.cli import upgrade_database
from incidentops.durable.config import WorkflowSettings
from incidentops.durable.runtime import create_runtime
from incidentops.models import Question
from tests.durable.test_workflow import current_document


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_real_postgres_migration_generation_publish_and_hybrid_search(
    tmp_path, acme, bravo, settings
):
    url = os.getenv("INCIDENTOPS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL with pgvector")
    cfg = WorkflowSettings(
        _env_file=None,
        provider="fake",
        tracing=False,
        workflow_database_url=url.replace("postgresql://", "postgresql+asyncpg://", 1),
        vector_backend="pgvector",
        vector_database_url=url,
        hnsw=True,
        object_root=tmp_path / "objects",
    )
    await asyncio.to_thread(upgrade_database, cfg)
    runtime = await create_runtime(cfg)
    try:
        await runtime.index.initialize()
        job = await runtime.submit_document(acme, str(uuid4()), current_document(settings))
        assert await runtime.run_job(job["id"])
        answer = await runtime.submit_question(
            acme, str(uuid4()), Question(question="Explain PUB-403")
        )
        assert await runtime.run_job(answer["id"])
        view = await runtime.job_view(acme, answer["id"])
        assert view["result"]["assessment"]["status"] == "answered"
        citation = view["result"]["assessment"]["claims"][0]["citation"]
        assert await runtime.repo.get_chunk(bravo, citation["chunk_id"]) is None
    finally:
        await runtime.aclose()


@pytest.mark.broker
@pytest.mark.asyncio
async def test_real_rabbitmq_dispatch_and_separate_worker_process(runtime, acme, tmp_path):
    url = os.getenv("INCIDENTOPS_TEST_BROKER_URL")
    if not url:
        pytest.skip("Requires a disposable RabbitMQ broker")
    cfg = runtime.settings.model_copy(update={"broker_url": __import__("pydantic").SecretStr(url)})
    application = create_celery_app(cfg)
    publisher = CeleryPublisher(application)
    job = await runtime.submit_question(acme, str(uuid4()), Question(question="Explain PUB-403"))
    env = os.environ | {
        "INCIDENTOPS_PROVIDER": "fake",
        "INCIDENTOPS_TRACING": "false",
        "INCIDENTOPS_VECTOR_BACKEND": "local",
        "INCIDENTOPS_OBJECT_BACKEND": "local",
        "INCIDENTOPS_WORKFLOW_DATABASE_URL": cfg.workflow_database_url,
        "INCIDENTOPS_OBJECT_ROOT": str(cfg.object_root),
        "INCIDENTOPS_BROKER_URL": url,
    }
    with (tmp_path / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "incidentops.durable.broker:app",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "-Q",
                "investigations",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            assert await dispatch_once(runtime.repo, publisher) == 1
            for _ in range(100):
                view = await runtime.job_view(acme, job["id"])
                if view["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.2)
            assert view["status"] == "succeeded"
            assert view["result"]["assessment"]["reason"] == "no_evidence"
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            await publisher.aclose()
            application.close()
