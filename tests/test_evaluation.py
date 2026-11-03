from pathlib import Path

from incidentops.evaluation import evaluate


def test_fake_evaluation_is_labelled_plumbing_only(service):
    report = evaluate(service, Path("data/eval/dev.jsonl"))
    assert report["provider"] == "fake"
    assert report["quality_evidence"] is False
    assert report["case_count"] >= 6
    assert report["answerability_accuracy"] == 1.0
    assert report["citation_integrity_rate"] == 1.0
    assert all(row["passed"] for row in report["cases"])


def test_eval_reports_corpus_and_prompt_versions(service):
    report = evaluate(service, Path("data/eval/dev.jsonl"))
    assert report["dataset_sha256"]
    assert report["prompt_version"] == "extractive-v1"
    assert report["indexed_corpus_sha256"]
    assert report["latency_ms"]["p95"] >= report["latency_ms"]["p50"]


def test_false_abstention_fails_positive_case(service):
    from incidentops.models import Draft

    service.generator.generate = lambda *args: Draft(answerable=False, claims=[])
    report = evaluate(service, Path("data/eval/dev.jsonl"))
    assert report["answerability_accuracy"] < 1.0
    assert report["all_passed"] is False
