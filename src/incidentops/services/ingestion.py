from collections.abc import Iterable

from langchain_core.embeddings import Embeddings
from langsmith import tracing_context

from incidentops.models import Chunk, SourceDocument
from incidentops.ports import ChunkWriter, DocumentChunker


class IngestionService:
    """Bounded fixture ingestion. Durable staged generations are the next milestone.

    Batching bounds each provider call. max_chunks bounds this use case's in-memory
    staging; it does not turn the snapshot writer into distributed ingestion.
    """

    def __init__(
        self,
        writer: ChunkWriter,
        embeddings: Embeddings,
        chunker: DocumentChunker,
        *,
        embedding_batch_size: int = 32,
        max_chunks: int = 2000,
    ):
        if embedding_batch_size <= 0 or max_chunks <= 0:
            raise ValueError("Ingestion limits must be positive")
        self.writer, self.embeddings, self.chunker = writer, embeddings, chunker
        self.embedding_batch_size, self.max_chunks = embedding_batch_size, max_chunks

    def ingest(self, documents: Iterable[SourceDocument]) -> int:
        versions = set()
        chunks: list[Chunk] = []
        for document in documents:
            key = (document.tenant_id, document.document_id, document.version)
            if key in versions:
                raise ValueError("Duplicate document version in corpus")
            versions.add(key)
            chunks.extend(self.chunker.split(document))
            if len(chunks) > self.max_chunks:
                raise ValueError("Ingestion exceeds maximum chunk count")
        if not chunks:
            raise ValueError("Cannot publish an empty ingestion snapshot")
        vectors = []
        # Suppress inherited automatic telemetry before crossing any provider boundary.
        with tracing_context(enabled=False):
            for offset in range(0, len(chunks), self.embedding_batch_size):
                batch = chunks[offset : offset + self.embedding_batch_size]
                embedded = self.embeddings.embed_documents([chunk.text for chunk in batch])
                if len(embedded) != len(batch):
                    raise ValueError("Embedding count must equal batch chunk count")
                vectors.extend(embedded)
        # No visible write until every batch succeeds. This remains a global trusted CLI.
        self.writer.replace_corpus(chunks, vectors)
        return len(chunks)
