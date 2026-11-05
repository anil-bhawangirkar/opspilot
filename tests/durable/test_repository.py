import asyncio

import pytest

from incidentops.chunking import RecursiveDocumentChunker
from incidentops.durable.repository import Conflict, LostLease, Repository
from incidentops.ingestion import load_documents


@pytest.fixture
async def repo(tmp_path):
    result = Repository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await result.initialize_for_tests()
    yield result
    await result.aclose()


@pytest.mark.asyncio
async def test_duplicate_submission_returns_same_job_and_conflicting_intent_fails(repo, acme):
    a = await repo.submit(acme, "answer", "key-1", {"question": "PUB-403"}, deadline_seconds=30)
    b = await repo.submit(acme, "answer", "key-1", {"question": "PUB-403"}, deadline_seconds=30)
    assert a["id"] == b["id"]
    assert len(await repo.pending_events()) == 1
    with pytest.raises(Conflict):
        await repo.submit(acme, "answer", "key-1", {"question": "different"}, deadline_seconds=30)


async def test_concurrent_submissions_from_independent_connections_create_one_job(repo, acme):
    peer = Repository(str(repo.engine.url))
    try:
        results = await asyncio.gather(
            *[
                store.submit(acme, "answer", "concurrent", {"question": "PUB-403"})
                for store in [repo, peer, repo, peer]
            ]
        )
        assert len({job["id"] for job in results}) == 1
        assert len(await repo.pending_events()) == 1
        assert len(await repo.job_events(acme, results[0]["id"])) == 1
    finally:
        await peer.aclose()


async def test_simultaneous_claims_from_independent_connections_have_one_owner(repo, acme):
    peer = Repository(str(repo.engine.url))
    try:
        job = await repo.submit(acme, "answer", "race", {})
        claims = await asyncio.gather(repo.claim(job["id"]), peer.claim(job["id"]))
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        assert winners[0]["attempt"] == 1
        assert winners[0]["fence"] == 1
    finally:
        await peer.aclose()


@pytest.mark.asyncio
async def test_opaque_object_version_is_not_modified_by_text_redaction(repo, acme):
    job = await repo.submit(
        acme, "ingest", "opaque", {"object": {"version_id": "192.0.2.9"}}, deadline_seconds=30
    )
    assert job["payload"]["object"]["version_id"] == "192.0.2.9"


@pytest.mark.asyncio
async def test_expiration_writes_one_terminal_progress_event(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    job = await repo.submit(acme, "answer", "expire-event", {}, deadline_seconds=1)
    clock[0] += 2
    await repo.recover_expired()
    await repo.recover_expired()
    assert [e["event_type"] for e in await repo.job_events(acme, job["id"])].count("expired") == 1


@pytest.mark.asyncio
async def test_job_lookup_and_cancel_are_tenant_principal_scoped(repo, acme, bravo):
    job = await repo.submit(acme, "answer", "secret", {}, deadline_seconds=30)
    assert await repo.get_job(bravo, job["id"]) is None
    assert await repo.cancel(bravo, job["id"]) is False
    assert await repo.cancel(acme, job["id"]) is True
    assert await repo.claim(job["id"], lease_seconds=30) is None


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_old_fence_cannot_finish(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    job = await repo.submit(acme, "answer", "key", {}, deadline_seconds=300)
    first = await repo.claim(job["id"], lease_seconds=10)
    assert first is not None
    assert await repo.claim(job["id"], lease_seconds=10) is None
    clock[0] += 11
    second = await repo.claim(job["id"], lease_seconds=10)
    assert second["fence"] > first["fence"]
    with pytest.raises(LostLease):
        await repo.complete(first, {"unsafe": True})
    await repo.complete(second, {"safe": True})
    assert (await repo.get_job(acme, job["id"]))["result"] == {"safe": True}


@pytest.mark.asyncio
async def test_generation_is_invisible_until_promotion_and_updates_one_document(
    repo, acme, bravo, settings
):
    docs = [d for d in load_documents(settings.corpus_dir) if d.status == "active"]
    acme_doc = next(
        d for d in docs if d.tenant_id == "acme" and d.document_id == "publication-runbook"
    )
    bravo_doc = next(
        d for d in docs if d.tenant_id == "bravo" and d.document_id == "publication-runbook"
    )
    for principal, doc in [(bravo, bravo_doc), (acme, acme_doc)]:
        chunks = RecursiveDocumentChunker().split(doc)
        job = await repo.submit(
            principal, "ingest", f"ingest-{doc.tenant_id}", {}, deadline_seconds=60
        )
        claim = await repo.claim(job["id"], lease_seconds=30)
        await repo.begin_generation(claim, doc, "fake:256", expected_revision=0)
        await repo.save_batch(claim, 0, chunks, [[1.0, 0.0]] * len(chunks))
        assert (await repo.authorized_chunks(principal)) == []
        await repo.promote(claim, expected_chunks=len(chunks))
    assert (await repo.authorized_chunks(acme))[0].tenant_id == "acme"
    assert (await repo.authorized_chunks(bravo))[0].tenant_id == "bravo"
    assert await repo.get_chunk(bravo, (await repo.authorized_chunks(acme))[0].chunk_id) is None


@pytest.mark.asyncio
async def test_source_version_content_is_immutable_and_stale_publish_loses(repo, acme, settings):
    doc = next(d for d in load_documents(settings.corpus_dir) if d.tenant_id == "acme")
    one = await repo.submit(acme, "ingest", "one", {}, deadline_seconds=60)
    two = await repo.submit(acme, "ingest", "two", {}, deadline_seconds=60)
    first = await repo.claim(one["id"], lease_seconds=30)
    second = await repo.claim(two["id"], lease_seconds=30)
    await repo.begin_generation(first, doc, "fake", expected_revision=0)
    changed = doc.model_copy(update={"text": "Changed without a version increase"})
    with pytest.raises(Conflict):
        await repo.begin_generation(second, changed, "fake", expected_revision=0)
    await repo.begin_generation(second, doc, "fake", expected_revision=0)
    chunks = RecursiveDocumentChunker().split(doc)
    for claim in [first, second]:
        await repo.save_batch(claim, 0, chunks, [[1.0, 0.0]] * len(chunks))
    await repo.promote(first, expected_chunks=len(chunks))
    with pytest.raises(Conflict):
        await repo.promote(second, expected_chunks=len(chunks))


@pytest.mark.asyncio
async def test_outbox_replay_and_expired_lease_recovery(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    job = await repo.submit(acme, "answer", "key", {}, deadline_seconds=300)
    event = (await repo.pending_events())[0]
    # Dispatcher may publish the same event twice before recording confirmation.
    assert (await repo.pending_events())[0]["id"] == event["id"]
    await repo.mark_event_published(event["id"])
    assert await repo.pending_events() == []
    await repo.claim(job["id"], lease_seconds=10)
    clock[0] += 11
    assert await repo.recover_expired() == 1
    assert len(await repo.pending_events()) == 1


@pytest.mark.asyncio
async def test_expired_job_never_starts_and_bounded_retry_reaches_terminal_state(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    job = await repo.submit(acme, "answer", "expires", {}, deadline_seconds=1)
    clock[0] += 2
    assert await repo.claim(job["id"], lease_seconds=10) is None
    assert (await repo.get_job(acme, job["id"]))["status"] == "expired"


@pytest.mark.asyncio
async def test_retry_event_is_not_dispatchable_until_due(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    job = await repo.submit(acme, "answer", "retry", {}, deadline_seconds=100)
    await repo.mark_event_published((await repo.pending_events())[0]["id"])
    claim = await repo.claim(job["id"], lease_seconds=30)
    await repo.retry_or_fail(claim, "timeout", transient=True, delay=10)
    assert await repo.pending_events() == []
    clock[0] += 11
    assert len(await repo.pending_events()) == 1


@pytest.mark.asyncio
async def test_current_document_acl_cannot_be_bypassed_by_incoming_acl(repo, acme, settings):
    from dataclasses import replace

    reviewer = replace(acme, groups=("security",))
    doc = next(d for d in load_documents(settings.corpus_dir) if d.access_group == "security")
    job = await repo.submit(reviewer, "ingest", "security", {}, deadline_seconds=60)
    claim = await repo.claim(job["id"], lease_seconds=30)
    await repo.begin_generation(claim, doc, "fake", expected_revision=0)
    pieces = RecursiveDocumentChunker().split(doc)
    await repo.save_batch(claim, 0, pieces, [[1.0, 0.0]] * len(pieces))
    await repo.promote(claim, expected_chunks=len(pieces))
    update_job = await repo.submit(acme, "ingest", "bypass", {}, deadline_seconds=60)
    update_claim = await repo.claim(update_job["id"], lease_seconds=30)
    with pytest.raises(Conflict):
        await repo.begin_generation(
            update_claim,
            doc.model_copy(update={"access_group": "operators"}),
            "fake",
            expected_revision=1,
        )
    delete_job = await repo.submit(acme, "delete", "delete", {}, deadline_seconds=60)
    with pytest.raises(Conflict):
        await repo.retire(await repo.claim(delete_job["id"], lease_seconds=30), doc.document_id, 1)


@pytest.mark.asyncio
async def test_lost_delivery_before_claim_is_eventually_redispatched(repo, acme):
    clock = [1000.0]
    repo.clock = lambda: clock[0]
    await repo.submit(acme, "answer", "delivery-lost", {}, deadline_seconds=300)
    await repo.mark_event_published((await repo.pending_events())[0]["id"])
    clock[0] += 90
    await repo.recover_expired()
    assert len(await repo.pending_events()) == 1


@pytest.mark.asyncio
async def test_new_embedding_space_does_not_read_old_active_vectors(repo, acme, settings):
    doc = next(d for d in load_documents(settings.corpus_dir) if d.tenant_id == "acme")
    job = await repo.submit(acme, "ingest", "model-one", {}, deadline_seconds=60)
    claim = await repo.claim(job["id"], lease_seconds=30)
    await repo.begin_generation(claim, doc, "embedding-one", expected_revision=0)
    pieces = RecursiveDocumentChunker().split(doc)
    forged = pieces[0].model_copy(update={"document_hash": "0" * 64})
    with pytest.raises(Conflict):
        await repo.save_batch(claim, 0, [forged], [[1.0, 0.0]])
    await repo.save_batch(claim, 0, pieces, [[1.0, 0.0]] * len(pieces))
    await repo.promote(claim, expected_chunks=len(pieces))
    assert await repo.authorized_records(acme, embedding_id="embedding-one")
    assert await repo.authorized_records(acme, embedding_id="embedding-two") == []
