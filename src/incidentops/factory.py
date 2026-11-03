from incidentops.config import Settings
from incidentops.pipeline import RAGService
from incidentops.providers import make_providers
from incidentops.store import Store
from incidentops.tracing import TraceSink


def build_service(settings: Settings) -> RAGService:
    if settings.tracing and settings.langsmith_api_key is None:
        raise ValueError("LANGSMITH_API_KEY is required when tracing is enabled")
    embeddings, generator, identity, dimensions = make_providers(settings)
    return RAGService(
        Store(settings.database_url, identity, dimensions),
        embeddings,
        generator,
        mode=settings.provider,
        top_k=settings.top_k,
        context_tokens=settings.context_tokens,
        trace_sink=TraceSink(
            settings.tracing,
            settings.trace_project,
            api_key=settings.langsmith_api_key.get_secret_value()
            if settings.langsmith_api_key
            else None,
        ),
    )
