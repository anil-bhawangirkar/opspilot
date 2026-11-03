import hashlib
import json
import re
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langsmith import tracing_context

from incidentops.models import Chunk, SourceDocument
from incidentops.security import redact, token_upper_bound
from incidentops.store import Store


def load_documents(path: Path) -> list[SourceDocument]:
    files = sorted(path.glob("*.json"))
    if not files:
        raise ValueError("No source documents found")
    return [SourceDocument.model_validate_json(file.read_text()) for file in files]


def chunk_document(document: SourceDocument, max_tokens: int) -> list[Chunk]:
    # Redact first: source offsets and hashes address the redacted representation.
    safe = document.model_copy(
        update={"text": redact(document.text), "title": redact(document.title)}
    )
    doc_hash = hashlib.sha256(safe.text.encode()).hexdigest()
    spans = []
    # Paragraph boundaries are the baseline. Oversized paragraphs split at whitespace.
    for paragraph in re.finditer(r"\S(?:[^\n]|\n(?!\n))*", safe.text):
        start, end = paragraph.span()
        while start < end:
            stop = start
            while stop < end and token_upper_bound(safe.text[start : stop + 1]) <= max_tokens:
                stop += 1
            if stop == start:
                raise ValueError("Chunk budget cannot fit a UTF-8 character")
            if stop < end:
                boundary = safe.text.rfind(" ", start, stop)
                if boundary > start:
                    stop = boundary
            spans.append((start, stop))
            start = stop
            while start < end and safe.text[start].isspace():
                start += 1
    # Pack adjacent paragraphs so headings do not consume a retrieval slot alone.
    grouped = []
    for start, end in spans:
        if grouped and token_upper_bound(safe.text[grouped[-1][0] : end]) <= max_tokens:
            grouped[-1] = (grouped[-1][0], end)
        else:
            grouped.append((start, end))
    result = []
    for index, (start, end) in enumerate(grouped):
        text = safe.text[start:end]
        digest = hashlib.sha256(text.encode()).hexdigest()
        identity = f"{safe.tenant_id}:{safe.document_id}:v{safe.version}:{index}:{digest}"
        chunk_id = hashlib.sha256(identity.encode()).hexdigest()
        result.append(
            Chunk(
                **safe.model_dump(exclude={"text"}),
                text=text,
                chunk_id=chunk_id,
                content_hash=digest,
                document_hash=doc_hash,
                start_offset=start,
                end_offset=end,
            )
        )
    return result


def ingest(
    source: Path | list[SourceDocument],
    store: Store,
    embeddings: Embeddings,
    max_tokens: int,
) -> int:
    documents = load_documents(source) if isinstance(source, Path) else source
    versions = [(d.tenant_id, d.document_id, d.version) for d in documents]
    if len(set(versions)) != len(versions):
        raise ValueError("Duplicate document version in corpus")
    chunks = [c for d in documents for c in chunk_document(d, max_tokens)]
    # Persist only after all embedding calls succeed; no half-updated corpus.
    with tracing_context(enabled=False):
        vectors = embeddings.embed_documents([c.text for c in chunks])
    store.replace_corpus(chunks, vectors)
    return len(chunks)


def corpus_fingerprint(path: Path) -> str:
    payload = [d.model_dump(mode="json") for d in load_documents(path)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
