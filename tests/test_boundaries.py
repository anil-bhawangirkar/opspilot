from dataclasses import replace

import pytest

from incidentops.config import Settings
from incidentops.ingestion import ingest, load_documents
from incidentops.models import Draft, Question
from incidentops.security import authenticate
from incidentops.store import Store


def test_empty_authorization_groups_never_match(service, acme):
    assert service.retrieve(replace(acme, groups=()), Question(question="PUB-403")) == []


def test_non_ascii_token_is_rejected_without_server_error():
    assert authenticate("caf\u00e9", "acme-demo-token", "bravo-demo-token") is None


def test_zero_query_vector_is_rejected_before_cosine_search(service, acme):
    with pytest.raises(ValueError, match="zero"):
        service.store.search(acme, Question(question="???"), [0.0] * 256, 5)


def test_embedding_space_cannot_change_silently(service, settings):
    with pytest.raises(ValueError, match="Embedding configuration"):
        Store(settings.database_url, "a-different-model", 256)


def test_sql_filter_values_are_parameters(service, acme):
    assert service.retrieve(acme, Question(question="PUB-403", region="west' OR 1=1 --")) == []


def test_revoked_evidence_metadata_is_not_returned(service, settings, acme):
    original = service.generator.generate

    def revoke(question, evidence):
        docs = load_documents(settings.corpus_dir)
        docs = [
            d.model_copy(update={"access_group": "security"}) if d.tenant_id == "acme" else d
            for d in docs
        ]
        ingest(docs, service.store, service.embeddings, settings.chunk_tokens)
        return original(question, evidence)

    service.generator.generate = revoke
    result = service.ask(acme, Question(question="Explain PUB-403"))
    assert result.status == "insufficient_evidence"
    assert result.retrieved == []


def test_failed_reingestion_leaves_old_corpus_available(service, settings, acme):
    docs = load_documents(settings.corpus_dir)
    docs[-1] = docs[-1].model_copy(update={"text": "An illegal in-place version edit."})
    with pytest.raises(ValueError):
        ingest(docs, service.store, service.embeddings, settings.chunk_tokens)
    assert service.ask(acme, Question(question="Explain PUB-403")).status == "answered"


def test_api_keys_can_be_loaded_from_dotenv_without_shell_export(tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text("OPENAI_API_KEY=synthetic-test-key\nLANGSMITH_API_KEY=synthetic-trace-key\n")
    cfg = Settings(_env_file=envfile)
    assert cfg.openai_api_key.get_secret_value() == "synthetic-test-key"
    assert cfg.langsmith_api_key.get_secret_value() == "synthetic-trace-key"


def test_evaluation_outage_never_counts_as_successful_abstention(service):
    from pathlib import Path

    from incidentops.evaluation import evaluate

    def fail(*args):
        raise TimeoutError("simulated")

    service.generator.generate = fail
    report = evaluate(service, Path("data/eval/dev.jsonl"))
    row = next(r for r in report["cases"] if r["id"] == "unknown-subject")
    assert row["passed"] is False


def test_false_answerable_with_claims_never_emits_claims(service, acme):
    service.generator.generate = lambda *args: Draft(answerable=False, claims=[])
    assert service.ask(acme, Question(question="Explain PUB-403")).claims == []
