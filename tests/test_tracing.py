import json

from langsmith import get_tracing_context

from incidentops.models import Question
from incidentops.tracing import TraceSink


class CapturingClient:
    def __init__(self):
        self.runs = []

    def create_run(self, **kwargs):
        self.runs.append(kwargs)


def test_trace_exports_allowlisted_metrics_without_source_or_identity(service, acme):
    client = CapturingClient()
    service.trace_sink = TraceSink(True, "test", client=client)
    service.ask(
        acme, Question(question="Explain PUB-403 for alice@example.test api_key=secret-value")
    )
    payload = json.dumps(client.runs, default=str)
    for private in ["alice@example.test", "secret-value", "acme", "PUB-403", "lacks the artifact"]:
        assert private not in payload
    assert "latency_ms" in payload
    assert len(client.runs) == 4
    assert all(r["end_time"] > r["start_time"] for r in client.runs)


def test_auto_tracing_is_disabled_inside_generator(service, acme):
    original = service.generator.generate

    def check(question, evidence):
        assert get_tracing_context()["enabled"] is False
        assert "alice@example.test" not in question
        return original(question, evidence)

    service.generator.generate = check
    service.ask(acme, Question(question="Explain PUB-403 for alice@example.test"))


def test_trace_export_failure_does_not_break_answer(service, acme):
    class BrokenClient:
        def create_run(self, **kwargs):
            raise ConnectionError("trace backend down")

    service.trace_sink = TraceSink(True, "test", client=BrokenClient())
    assert service.ask(acme, Question(question="Explain PUB-403")).status == "answered"
