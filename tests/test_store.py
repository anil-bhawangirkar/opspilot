from dataclasses import replace
from datetime import date

import pytest

from incidentops.ingestion import ingest, load_documents
from incidentops.models import Question


def test_dense_search_never_ranks_another_tenant(service, acme):
    evidence = service.retrieve(acme, Question(question="PUB-403 publication failure"))
    assert evidence
    assert {e.chunk.tenant_id for e in evidence} == {"acme"}


def test_restricted_document_cannot_be_fetched(service, acme):
    privileged = replace(acme, groups=("security",))
    found = service.store.search(
        privileged, Question(question="security"), service.embeddings.embed_query("security"), 20
    )
    restricted = next(e.chunk for e in found if e.chunk.document_id == "security-note")
    assert service.store.get_chunk(acme, restricted.chunk_id) is None
    assert service.store.get_chunk(privileged, restricted.chunk_id) is not None


def test_stale_and_future_sources_are_excluded(service, acme):
    evidence = service.store.search(
        acme,
        Question(question="workers"),
        service.embeddings.embed_query("workers"),
        100,
        as_of=date(2026, 11, 3),
    )
    assert evidence
    ids = {(e.chunk.document_id, e.chunk.version) for e in evidence}
    assert ("publication-runbook", 1) not in ids
    assert ("future-runbook", 1) not in ids
    assert ("publication-runbook", 2) in ids


def test_reingestion_keeps_chunk_ids_stable(service, settings, acme):
    before = service.retrieve(acme, Question(question="PUB-403"))
    ingest(settings.corpus_dir, service.store, service.embeddings, settings.chunk_tokens)
    after = service.retrieve(acme, Question(question="PUB-403"))
    assert [e.chunk.chunk_id for e in before] == [e.chunk.chunk_id for e in after]


def test_reused_document_version_with_changed_content_is_rejected(service, settings):
    docs = load_documents(settings.corpus_dir)
    docs[0] = docs[0].model_copy(update={"text": docs[0].text + " Changed content."})
    with pytest.raises(ValueError, match="version"):
        ingest(docs, service.store, service.embeddings, settings.chunk_tokens)


def test_wrong_region_returns_no_evidence(service, acme):
    assert service.retrieve(acme, Question(question="PUB-403", region="moon")) == []


def test_other_tenant_citation_lookup_returns_nothing(service, acme, bravo):
    evidence = service.retrieve(bravo, Question(question="PUB-403"))
    assert evidence
    assert service.store.get_chunk(acme, evidence[0].chunk.chunk_id) is None
