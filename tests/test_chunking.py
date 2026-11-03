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
