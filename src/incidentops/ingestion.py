"""Compatibility CLI helpers; splitting and ingestion use cases live in their own modules."""

import hashlib
import json
from pathlib import Path

from langchain_core.embeddings import Embeddings

from incidentops.chunking import RecursiveDocumentChunker
from incidentops.models import Chunk, SourceDocument
from incidentops.ports import ChunkWriter
from incidentops.services.ingestion import IngestionService


def load_documents(path: Path) -> list[SourceDocument]:
    files = sorted(path.glob("*.json"))
    if not files:
        raise ValueError("No source documents found")
    return [SourceDocument.model_validate_json(file.read_text()) for file in files]


def chunk_document(
    document: SourceDocument,
    max_characters: int,
    overlap_chars: int = 0,
) -> list[Chunk]:
    """Compatibility adapter; size is characters, not the old conservative byte bound."""
    return RecursiveDocumentChunker(max_characters, overlap_chars).split(document)


def ingest(
    source: Path | list[SourceDocument],
    store: ChunkWriter,
    embeddings: Embeddings,
    max_characters: int,
    *,
    overlap_chars: int = 0,
    embedding_batch_size: int = 32,
    max_chunks: int = 2000,
) -> int:
    documents = load_documents(source) if isinstance(source, Path) else source
    return IngestionService(
        store,
        embeddings,
        RecursiveDocumentChunker(max_characters, overlap_chars),
        embedding_batch_size=embedding_batch_size,
        max_chunks=max_chunks,
    ).ingest(documents)


def corpus_fingerprint(path: Path) -> str:
    payload = [d.model_dump(mode="json") for d in load_documents(path)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
