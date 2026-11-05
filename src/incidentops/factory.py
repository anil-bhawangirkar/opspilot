from incidentops.chunking import RecursiveDocumentChunker
from incidentops.config import Settings
from incidentops.pipeline import RAGService
from incidentops.providers import make_embeddings, make_providers
from incidentops.services.ingestion import IngestionService
from incidentops.services.retrieval import DenseRetriever
from incidentops.store import Store
from incidentops.tracing import TraceSink


def build_service(settings: Settings) -> RAGService:
    if settings.tracing and settings.langsmith_api_key is None:
        raise ValueError("LANGSMITH_API_KEY is required when tracing is enabled")
    embeddings, generator, identity, dimensions = make_providers(settings)
    store = Store(settings.database_url, identity, dimensions)
    return RAGService(
        store,
        embeddings,
        generator,
        mode=settings.provider,
        top_k=settings.top_k,
        context_tokens=settings.context_tokens,
        retriever=DenseRetriever(store, embeddings, top_k=settings.top_k),
        trace_sink=TraceSink(
            settings.tracing,
            settings.trace_project,
            api_key=settings.langsmith_api_key.get_secret_value()
            if settings.langsmith_api_key
            else None,
        ),
    )


def build_ingestion_service(settings: Settings) -> IngestionService:
    embeddings, identity, dimensions = make_embeddings(settings)
    return IngestionService(
        Store(settings.database_url, identity, dimensions),
        embeddings,
        RecursiveDocumentChunker(settings.chunk_size_chars, settings.chunk_overlap_chars),
        embedding_batch_size=settings.embedding_batch_size,
        max_chunks=settings.ingestion_max_chunks,
    )
