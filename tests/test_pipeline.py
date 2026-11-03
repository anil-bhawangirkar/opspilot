import json

from incidentops.models import Draft, ProposedClaim, Question
from incidentops.security import redact


def test_answer_contains_verified_quote_and_versioned_citation(service, acme):
    result = service.ask(acme, Question(question="What does PUB-403 mean in release 2.7?"))
    assert result.status == "answered"
    assert result.mode == "fake"
    assert result.claims
    for claim in result.claims:
        source = service.store.get_chunk(acme, claim.citation.chunk_id)
        assert claim.quote in source.text
        assert claim.citation.content_hash == source.content_hash


def test_unanswerable_question_abstains(service, acme):
    result = service.ask(acme, Question(question="What is the CEO's favourite sandwich?"))
    assert result.status == "insufficient_evidence"
    assert result.claims == []


def test_no_sources_abstains_without_calling_generator(service, acme):
    service.generator.generate = lambda *args: (_ for _ in ()).throw(AssertionError("called"))
    result = service.ask(acme, Question(question="PUB-403", region="unknown"))
    assert result.reason == "no_evidence"


def test_forged_quote_is_not_released(service, acme):
    item = service.retrieve(acme, Question(question="PUB-403"))[0]
    service.generator.generate = lambda *args: Draft(
        answerable=True,
        claims=[
            ProposedClaim(
                evidence_id=item.chunk.chunk_id, quote="Restarting workers fixes everything."
            )
        ],
    )
    result = service.ask(acme, Question(question="PUB-403"))
    assert result.reason == "invalid_grounding"
    assert not result.claims


def test_unknown_citation_is_not_released(service, acme):
    service.generator.generate = lambda *args: Draft(
        answerable=True,
        claims=[ProposedClaim(evidence_id="invented", quote="some plausible quote")],
    )
    assert service.ask(acme, Question(question="PUB-403")).reason == "invalid_grounding"


def test_provider_failure_abstains_without_exporting_error_text(service, acme):
    def fail(*args):
        raise TimeoutError("secret=synthetic-sensitive-value alice@example.test")

    service.generator.generate = fail
    result = service.ask(acme, Question(question="PUB-403"))
    assert result.reason == "provider_error"
    assert "sensitive-value" not in result.model_dump_json()


def test_malformed_structured_output_abstains(service, acme):
    service.generator.generate = lambda *args: {"answer": "trust me"}
    assert service.ask(acme, Question(question="PUB-403")).reason == "invalid_output"


def test_context_budget_is_enforced(service, acme):
    service.context_tokens = 30
    result = service.ask(acme, Question(question="PUB-403"))
    assert result.metrics.context_tokens <= 30


def test_redaction_covers_nested_trace_payloads():
    raw = {
        "question": "alice@example.test Bearer test-token-123",
        "nested": [
            "api_key=synthetic-credential-xyz",
            "client 192.0.2.99",
            "sk-" + "A" * 30,
        ],
    }
    safe = json.dumps(redact(raw))
    for private in [
        "alice@example.test",
        "test-token-123",
        "synthetic-credential-xyz",
        "192.0.2.99",
        "A" * 30,
    ]:
        assert private not in safe


def test_ingested_sensitive_text_is_redacted(service, acme):
    result = service.store.search(
        acme, Question(question="contact"), service.embeddings.embed_query("contact"), 100
    )
    text = " ".join(e.chunk.text for e in result)
    assert "alice@example.test" not in text
    assert "synthetic-secret-123" not in text
