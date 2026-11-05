import pytest
from langchain_core.documents import Document

from incidentops.chunking import RecursiveDocumentChunker, to_langchain_document
from incidentops.ingestion import chunk_document, load_documents
from incidentops.security import token_upper_bound


def test_short_heading_stays_with_supporting_paragraph(settings):
    doc = next(
        d
        for d in load_documents(settings.corpus_dir)
        if d.document_id == "publication-runbook" and d.version == 2
    )
    chunks = chunk_document(doc, 350)
    assert "PUB-403 indicates" in chunks[0].text
    assert chunks[0].text.startswith("# Publication")
    assert all(token_upper_bound(c.text) <= 350 for c in chunks)


def source_with_text(settings, text):
    return load_documents(settings.corpus_dir)[0].model_copy(update={"text": text})


def test_langchain_document_is_redacted_and_retains_typed_source_metadata(settings):
    source = source_with_text(settings, "Contact alice@example.test api_key=synthetic-secret")
    converted = to_langchain_document(source)
    assert isinstance(converted, Document)
    assert "alice@example.test" not in converted.page_content
    assert "synthetic-secret" not in converted.page_content
    assert converted.metadata["tenant_id"] == source.tenant_id
    assert converted.metadata["version"] == source.version
    assert "text" not in converted.metadata


def test_recursive_splitter_preserves_unicode_and_repeated_text_offsets(settings):
    source = source_with_text(settings, "同じ文です。 " * 25 + "\n\nA final paragraph.")
    chunks = RecursiveDocumentChunker(size_chars=40, overlap_chars=8).split(source)
    assert len(chunks) > 1
    for chunk in chunks:
        assert source.text[chunk.start_offset : chunk.end_offset] == chunk.text
        assert len(chunk.text) <= 40
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_overlap_keeps_context_on_long_unbroken_text(settings):
    source = source_with_text(settings, "abcdefghijklmnopqrstuvwxyz0123456789")
    chunks = RecursiveDocumentChunker(size_chars=20, overlap_chars=5).split(source)
    assert chunks[0].text[-5:] == chunks[1].text[:5]


def test_chunk_identity_changes_with_splitter_profile(settings):
    source = source_with_text(settings, "A short sentence.")
    a = RecursiveDocumentChunker(size_chars=100, overlap_chars=0).split(source)
    b = RecursiveDocumentChunker(size_chars=120, overlap_chars=0).split(source)
    assert a[0].text == b[0].text
    assert a[0].chunk_id != b[0].chunk_id
    assert a[0].document_hash == b[0].document_hash
    assert a == RecursiveDocumentChunker(size_chars=100, overlap_chars=0).split(source)


@pytest.mark.parametrize("size,overlap", [(0, 0), (10, -1), (10, 10), (10, 20)])
def test_invalid_chunking_profile_is_rejected(size, overlap):
    with pytest.raises(ValueError):
        RecursiveDocumentChunker(size_chars=size, overlap_chars=overlap)


def test_whitespace_only_document_is_rejected(settings):
    with pytest.raises(ValueError, match="empty"):
        RecursiveDocumentChunker().split(source_with_text(settings, " \n\n  "))
