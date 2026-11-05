import pytest

from incidentops.chunking import RecursiveDocumentChunker
from incidentops.factory import build_ingestion_service
from incidentops.ingestion import load_documents
from incidentops.models import Question
from incidentops.providers import FakeEmbeddings
from incidentops.services.ingestion import IngestionService


class RecordingEmbeddings(FakeEmbeddings):
    def __init__(self, fail_batch=None):
        self.batches = []
        self.fail_batch = fail_batch

    def embed_documents(self, texts):
        self.batches.append(texts)
        if len(self.batches) == self.fail_batch:
            raise TimeoutError("injected batch failure")
        return super().embed_documents(texts)


def test_embedding_batches_are_bounded_and_redacted(service, settings):
    embeddings = RecordingEmbeddings()
    ingestor = IngestionService(
        service.store,
        embeddings,
        RecursiveDocumentChunker(150, 0),
        embedding_batch_size=2,
        max_chunks=100,
    )
    count = ingestor.ingest(load_documents(settings.corpus_dir))
    assert count > 2
    assert all(1 <= len(batch) <= 2 for batch in embeddings.batches)
    assert sum(map(len, embeddings.batches)) == count
    assert "alice@example.test" not in str(embeddings.batches)
    assert "synthetic-secret-123" not in str(embeddings.batches)


def test_failure_in_later_batch_preserves_visible_snapshot(service, settings, acme):
    before = service.store.snapshot_fingerprint()
    ingestor = IngestionService(
        service.store,
        RecordingEmbeddings(fail_batch=2),
        RecursiveDocumentChunker(150, 0),
        embedding_batch_size=2,
    )
    with pytest.raises(TimeoutError):
        ingestor.ingest(load_documents(settings.corpus_dir))
    assert service.store.snapshot_fingerprint() == before
    assert service.ask(acme, Question(question="Explain PUB-403")).status == "answered"


def test_chunk_limit_rejects_before_embedding(service, settings):
    embeddings = RecordingEmbeddings()
    ingestor = IngestionService(
        service.store, embeddings, RecursiveDocumentChunker(100, 0), max_chunks=1
    )
    with pytest.raises(ValueError, match="chunk"):
        ingestor.ingest(load_documents(settings.corpus_dir))
    assert embeddings.batches == []


def test_ingestion_factory_does_not_initialize_a_chat_generator(settings, monkeypatch):
    from incidentops import providers

    def forbidden(*args, **kwargs):
        raise AssertionError("ingestion must not construct a chat generator")

    monkeypatch.setattr(providers, "FakeGenerator", forbidden)
    ingestor = build_ingestion_service(settings)
    assert ingestor.ingest(load_documents(settings.corpus_dir)) > 0


def test_empty_ingestion_cannot_erase_existing_snapshot(service):
    before = service.store.snapshot_fingerprint()
    ingestor = IngestionService(service.store, RecordingEmbeddings(), RecursiveDocumentChunker())
    with pytest.raises(ValueError, match="empty"):
        ingestor.ingest([])
    assert service.store.snapshot_fingerprint() == before


def test_malformed_embedding_batch_never_publishes(service, settings):
    class MissingVector(RecordingEmbeddings):
        def embed_documents(self, texts):
            return super().embed_documents(texts)[:-1]

    before = service.store.snapshot_fingerprint()
    ingestor = IngestionService(service.store, MissingVector(), RecursiveDocumentChunker())
    with pytest.raises(ValueError, match="count"):
        ingestor.ingest(load_documents(settings.corpus_dir))
    assert service.store.snapshot_fingerprint() == before


@pytest.mark.parametrize("batch_size,max_chunks", [(0, 10), (2, 0)])
def test_invalid_ingestion_limits_rejected(service, batch_size, max_chunks):
    with pytest.raises(ValueError):
        IngestionService(
            service.store,
            FakeEmbeddings(),
            RecursiveDocumentChunker(),
            embedding_batch_size=batch_size,
            max_chunks=max_chunks,
        )
