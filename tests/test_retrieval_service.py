from incidentops.models import Question
from incidentops.pipeline import RAGService
from incidentops.providers import FakeEmbeddings, FakeGenerator
from incidentops.services.retrieval import DenseRetriever
from incidentops.tracing import TraceSink


def test_retriever_redacts_query_before_embedding(service, acme):
    class RecordingEmbeddings(FakeEmbeddings):
        def embed_query(self, text):
            assert "alice@example.test" not in text
            return super().embed_query(text)

    retriever = DenseRetriever(service.store, RecordingEmbeddings())
    assert retriever.retrieve(acme, Question(question="Explain PUB-403 for alice@example.test"))


def test_rag_can_use_read_only_source_reader_and_independent_retriever(service, acme):
    class Reader:
        # Deliberately has no search, ingestion, delete or embedding methods.
        def get_chunk(self, principal, chunk_id):
            return service.store.get_chunk(principal, chunk_id)

    rag = RAGService(
        Reader(),
        service.embeddings,
        FakeGenerator(),
        mode="fake",
        top_k=5,
        context_tokens=1800,
        trace_sink=TraceSink(False, "test"),
        retriever=DenseRetriever(service.store, service.embeddings),
    )
    assert rag.ask(acme, Question(question="Explain PUB-403")).status == "answered"
