import os

import pytest

from incidentops.factory import build_service
from incidentops.ingestion import ingest
from incidentops.models import Question


@pytest.mark.postgres
def test_pgvector_roundtrip_and_tenant_filter(settings, acme, bravo):
    url = os.getenv("INCIDENTOPS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires a disposable PostgreSQL database with pgvector")
    settings.database_url = url
    service = build_service(settings)
    ingest(settings.corpus_dir, service.store, service.embeddings, settings.chunk_tokens)
    answer = service.ask(acme, Question(question="Explain PUB-403"))
    assert answer.status == "answered"
    assert "publish permission" in answer.claims[0].quote
    assert service.store.get_chunk(bravo, answer.claims[0].citation.chunk_id) is None
