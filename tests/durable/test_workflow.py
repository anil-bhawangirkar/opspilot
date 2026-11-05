from dataclasses import replace

import pytest

from incidentops.durable.runtime import create_runtime
from incidentops.ingestion import load_documents
from incidentops.models import Question


def current_document(settings):
    return next(
        d
        for d in load_documents(settings.corpus_dir)
        if d.tenant_id == "acme" and d.document_id == "publication-runbook" and d.version == 2
    )


@pytest.mark.asyncio
async def test_submission_is_durable_and_another_runtime_process_can_execute(
    runtime, acme, settings
):
    job = await runtime.submit_document(acme, "ingest", current_document(settings))
    assert job["status"] == "queued"
    assert await runtime.repo.authorized_chunks(acme) == []
    other = await create_runtime(runtime.settings)
    try:
        await other.run_job(job["id"])
    finally:
        await other.aclose()
    assert (await runtime.job_view(acme, job["id"]))["status"] == "succeeded"
    assert await runtime.repo.authorized_chunks(acme)
    again = await runtime.submit_document(acme, "ingest", current_document(settings))
    assert again["id"] == job["id"]
    assert await runtime.run_job(job["id"]) is False


@pytest.mark.asyncio
async def test_answer_is_queued_and_polled_with_verified_sources(runtime, acme, settings):
    ingestion = await runtime.submit_document(acme, "ingest", current_document(settings))
    await runtime.run_job(ingestion["id"])
    job = await runtime.submit_question(acme, "ask", Question(question="Explain PUB-403"))
    assert job["status"] == "queued" and job["result"] is None
    await runtime.run_job(job["id"])
    view = await runtime.job_view(acme, job["id"])
    assert view["status"] == "succeeded"
    assert view["result"]["assessment"]["status"] == "answered"
    assert view["result"]["retrieval"]["mode"] == "bm25+dense-rrf"


@pytest.mark.parametrize("status,expected", [(503, "retry"), (403, "failed")])
async def test_provider_server_error_retries_but_auth_error_is_terminal(
    runtime, acme, settings, status, expected
):
    import httpx
    from openai import APIStatusError

    ingestion = await runtime.submit_document(acme, "ingest", current_document(settings))
    await runtime.run_job(ingestion["id"])

    async def fail_model(question, evidence):
        raise APIStatusError(
            "synthetic-sensitive-provider-detail",
            response=httpx.Response(
                status, request=httpx.Request("POST", "https://provider.invalid/test")
            ),
            body=None,
        )

    runtime.generator.agenerate = fail_model
    job = await runtime.submit_question(acme, "ask-fails", Question(question="Explain PUB-403"))
    assert await runtime.run_job(job["id"]) is False
    view = await runtime.job_view(acme, job["id"])
    assert view["status"] == expected
    assert "synthetic-sensitive-provider-detail" not in str(view)
    assert view["result"] is None


@pytest.mark.asyncio
async def test_progressive_update_preserves_other_documents_and_stale_answer_is_withheld(
    runtime, acme, settings
):
    doc = current_document(settings)
    another = next(
        d
        for d in load_documents(settings.corpus_dir)
        if d.document_id == "publisher-architecture" and d.tenant_id == "acme"
    )
    for key, source in [("one", doc), ("two", another)]:
        job = await runtime.submit_document(acme, key, source)
        await runtime.run_job(job["id"])
    answer = await runtime.submit_question(acme, "answer", Question(question="Explain PUB-403"))
    await runtime.run_job(answer["id"])
    updated = doc.model_copy(update={"version": 3, "text": doc.text + "\nNew release note."})
    job = await runtime.submit_document(acme, "update", updated)
    await runtime.run_job(job["id"])
    records = await runtime.repo.authorized_chunks(acme)
    assert {c.document_id for c in records} == {doc.document_id, another.document_id}
    assert all(c.version == 3 for c in records if c.document_id == doc.document_id)
    assert (await runtime.job_view(acme, answer["id"]))["result"]["assessment"]["claims"] == []


@pytest.mark.asyncio
async def test_completed_batch_is_reused_after_vector_write_timeout(runtime, acme, settings):
    original_embed = runtime.embeddings.aembed_documents
    calls = []

    async def track(texts):
        calls.append(texts)
        return await original_embed(texts)

    runtime.embeddings.aembed_documents = track
    original_upsert = runtime.index.upsert
    state = [True]

    async def fail_once(*args):
        if state[0]:
            state[0] = False
            raise TimeoutError("after relational checkpoint, before vector acknowledgement")
        return await original_upsert(*args)

    runtime.index.upsert = fail_once
    job = await runtime.submit_document(acme, "resume", current_document(settings))
    await runtime.run_job(job["id"])
    assert (await runtime.job_view(acme, job["id"]))["status"] == "retry"
    runtime.repo.clock = lambda: __import__("time").time() + 20
    await runtime.run_job(job["id"])
    assert (await runtime.job_view(acme, job["id"]))["status"] == "succeeded"
    # A resumed generation reuses the first persisted embedding batch.
    first = calls[0]
    assert calls.count(first) == 1


@pytest.mark.asyncio
async def test_current_acl_revocation_also_hides_stored_answers(runtime, acme, settings):
    doc = current_document(settings)
    job = await runtime.submit_document(acme, "ingest", doc)
    await runtime.run_job(job["id"])
    answer = await runtime.submit_question(acme, "answer", Question(question="Explain PUB-403"))
    await runtime.run_job(answer["id"])
    privileged = replace(acme, groups=("operators", "security"))
    revoked = doc.model_copy(update={"access_group": "security"})
    job = await runtime.submit_document(privileged, "revoke", revoked)
    await runtime.run_job(job["id"])
    assert await runtime.repo.authorized_chunks(acme) == []
    view = await runtime.job_view(acme, answer["id"])
    assert view["result"]["assessment"]["claims"] == []


@pytest.mark.asyncio
async def test_unauthorized_tenant_and_cancellation_never_call_model(
    runtime, acme, bravo, settings
):
    with pytest.raises(PermissionError):
        await runtime.submit_document(bravo, "bad", current_document(settings))
    job = await runtime.submit_question(acme, "cancel", Question(question="Explain PUB-403"))
    assert await runtime.repo.cancel(acme, job["id"])
    assert await runtime.run_job(job["id"]) is False
    assert await runtime.job_view(bravo, job["id"]) is None


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_long_model_await(runtime, acme, settings):
    import asyncio

    from incidentops.durable.repository import LostLease

    job = await runtime.submit_document(acme, "seed", current_document(settings))
    await runtime.run_job(job["id"])
    runtime.settings.lease_seconds = 0.03

    async def lost(*args):
        raise LostLease("simulated ownership loss")

    async def forever(*args):
        await asyncio.Event().wait()

    runtime.repo.heartbeat = lost
    runtime.generator.agenerate = forever
    answer = await runtime.submit_question(acme, "long", Question(question="Explain PUB-403"))
    assert await asyncio.wait_for(runtime.run_job(answer["id"]), timeout=0.3) is False


@pytest.mark.asyncio
async def test_incremental_update_reuses_unchanged_text_embeddings_within_tenant(
    runtime, acme, bravo, settings
):
    doc = current_document(settings)
    original = runtime.embeddings.aembed_documents
    calls = []

    async def record(texts):
        calls.extend(texts)
        return await original(texts)

    runtime.embeddings.aembed_documents = record
    job = await runtime.submit_document(acme, "initial", doc)
    await runtime.run_job(job["id"])
    first = calls[0]
    updated = doc.model_copy(
        update={"version": 3, "text": doc.text + "\nA new short operational note."}
    )
    job = await runtime.submit_document(acme, "update", updated)
    await runtime.run_job(job["id"])
    assert calls.count(first) == 1
    other = doc.model_copy(update={"tenant_id": "bravo"})
    job = await runtime.submit_document(bravo, "other", other)
    await runtime.run_job(job["id"])
    assert calls.count(first) == 2


@pytest.mark.asyncio
async def test_v2_trace_exports_only_safe_summary(runtime, acme, settings):
    from incidentops.tracing import TraceSink
    from tests.test_tracing import CapturingClient

    client = CapturingClient()
    runtime.trace_sink = TraceSink(True, "test", client=client)
    job = await runtime.submit_document(acme, "seed-trace", current_document(settings))
    await runtime.run_job(job["id"])
    answer = await runtime.submit_question(
        acme, "trace", Question(question="Explain PUB-403 for alice@example.test")
    )
    await runtime.run_job(answer["id"])
    assert client.runs
    assert "alice@example.test" not in str(client.runs)
    assert "PUB-403" not in str(client.runs)


@pytest.mark.asyncio
async def test_revoked_duplicate_provenance_is_removed_from_stored_result(runtime, acme, settings):
    doc = current_document(settings).model_copy(
        update={
            "text": (
                "PUB-403 indicates that the image publisher lacks "
                "the artifact registry publish permission."
            )
        }
    )
    duplicate = doc.model_copy(update={"document_id": "duplicate-runbook"})
    for key, piece in [("original", doc), ("duplicate", duplicate)]:
        job = await runtime.submit_document(acme, key, piece)
        await runtime.run_job(job["id"])
    answer = await runtime.submit_question(acme, "answer", Question(question="Explain PUB-403"))
    await runtime.run_job(answer["id"])
    view = await runtime.job_view(acme, answer["id"])
    representative, ids = next(iter(view["result"]["retrieval"]["duplicate_sources"].items()))
    other_id = next(identifier for identifier in ids if identifier != representative)
    other = await runtime.repo.get_chunk(acme, other_id)
    job = await runtime.submit_delete(acme, "retire-duplicate", other.document_id, 1)
    await runtime.run_job(job["id"])
    view = await runtime.job_view(acme, answer["id"])
    assert view["result"]["assessment"]["status"] == "answered"
    assert other_id not in str(view)
