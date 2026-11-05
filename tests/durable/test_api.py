import httpx
import pytest

from incidentops.durable.api import create_workflow_app
from tests.durable.test_workflow import current_document


@pytest.mark.asyncio
async def test_api_accepts_without_running_llm_and_requires_authorized_polling(runtime):
    app = create_workflow_app(runtime.settings, runtime=runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            auth = {"Authorization": "Bearer acme-demo-token", "Idempotency-Key": "first"}
            response = await client.post(
                "/v2/answer-jobs", headers=auth, json={"question": "Explain PUB-403"}
            )
            assert response.status_code == 202
            assert response.json()["status"] == "queued"
            url = response.headers["Location"]
            assert (await client.get(url, headers=auth)).status_code == 200
            assert (
                await client.get(url, headers={"Authorization": "Bearer bravo-demo-token"})
            ).status_code == 404
            assert (
                await client.post("/v2/answer-jobs", json={"question": "Explain PUB-403"})
            ).status_code == 401
            assert (
                await client.post("/v2/answer-jobs", headers=auth, json={"question": "different"})
            ).status_code == 409


@pytest.mark.asyncio
async def test_ingestion_api_and_document_scoped_deletion(runtime, settings):
    app = create_workflow_app(runtime.settings, runtime=runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            auth = {"Authorization": "Bearer acme-demo-token", "Idempotency-Key": "ingest"}
            response = await client.post(
                "/v2/ingestion-jobs",
                headers=auth,
                json={"document": current_document(settings).model_dump(mode="json")},
            )
            assert response.status_code == 202
            await runtime.run_job(response.json()["id"])
            result = await client.post(
                "/v2/retrieve", headers=auth, json={"question": "Explain PUB-403"}
            )
            assert result.status_code == 200 and result.json()["evidence"]
            url = result.json()["evidence"][0]["citation"]["source_url"]
            assert (await client.get(url, headers=auth)).status_code == 200
            auth["Idempotency-Key"] = "delete"
            deleted = await client.post(
                "/v2/deletion-jobs",
                headers=auth,
                json={"document_id": "publication-runbook", "expected_revision": 1},
            )
            await runtime.run_job(deleted.json()["id"])
            assert (await client.get(url, headers=auth)).status_code == 404


@pytest.mark.asyncio
async def test_completed_job_events_are_replayable_without_source_text(runtime):
    from incidentops.models import Principal, Question

    actor = Principal("acme", "acme-demo-operator", ("operators",))
    job = await runtime.submit_question(actor, "events", Question(question="Explain PUB-403"))
    await runtime.run_job(job["id"])
    app = create_workflow_app(runtime.settings, runtime=runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v2/jobs/{job['id']}/events", headers={"Authorization": "Bearer acme-demo-token"}
            )
            assert response.status_code == 200
            assert "event: succeeded" in response.text
            assert "PUB-403" not in response.text


@pytest.mark.asyncio
async def test_large_body_is_rejected_before_json_parsing_including_chunked_input(runtime):
    cfg = runtime.settings.model_copy(update={"max_request_bytes": 1024})
    app = create_workflow_app(cfg, runtime=runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post("/v2/answer-jobs", content=b"x" * 2048)).status_code == 413

            async def chunks():
                yield b"x" * 600
                yield b"x" * 600

            assert (await client.post("/v2/answer-jobs", content=chunks())).status_code == 413


@pytest.mark.asyncio
async def test_event_stream_drains_all_pages_before_ending(runtime):
    from incidentops.models import Principal, Question

    actor = Principal("acme", "acme-demo-operator", ("operators",))
    job = await runtime.submit_question(actor, "many-events", Question(question="Explain PUB-403"))
    async with runtime.repo.engine.begin() as connection:
        for ordinal in range(150):
            await runtime.repo._event(connection, job["id"], "batch_checkpoint", {"batch": ordinal})
    await runtime.run_job(job["id"])
    app = create_workflow_app(runtime.settings, runtime=runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v2/jobs/{job['id']}/events", headers={"Authorization": "Bearer acme-demo-token"}
            )
            assert "event: succeeded" in response.text
            assert '"batch": 149' in response.text
