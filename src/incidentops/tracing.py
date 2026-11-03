"""An allowlisted telemetry boundary; raw nested LangChain traces stay disabled."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from langsmith import Client

from incidentops.models import Assessment
from incidentops.security import redact

logger = logging.getLogger(__name__)


def safe_summary(result: Assessment) -> dict:
    # Never accept question, principal, source text, quote, exception or arbitrary metadata here.
    return redact(
        {
            "mode": result.mode,
            "status": result.status,
            "reason": result.reason,
            "claim_count": len(result.claims),
            "retrieved_chunks": result.metrics.retrieved_chunks,
            "context_token_upper_bound": result.metrics.context_tokens,
            "latency_ms": result.metrics.latency_ms,
            "prompt_version": result.metrics.prompt_version,
        }
    )


class TraceSink:
    def __init__(self, enabled: bool, project: str, client=None, api_key=None):
        self.enabled, self.project = enabled, project
        self.client = client
        if enabled and client is None:
            self.client = Client(
                hide_inputs=True,
                hide_outputs=redact,
                hide_metadata=True,
                omit_traced_runtime_info=True,
                timeout_ms=3000,
                api_key=api_key,
            )

    def emit(self, result: Assessment, stages: list[tuple[str, float]]) -> None:
        if not self.enabled:
            return
        try:
            now = datetime.now(UTC)
            started = now - timedelta(milliseconds=result.metrics.latency_ms)
            parent_id = UUID(result.run_id)
            self.client.create_run(
                id=parent_id,
                name="incidentops.assessment",
                run_type="chain",
                inputs={},
                outputs=safe_summary(result),
                project_name=self.project,
                start_time=started,
                end_time=now,
            )
            # Real stage durations, without request bodies or graph state.
            cursor = started
            for name, latency in stages:
                ended = cursor + timedelta(milliseconds=latency)
                self.client.create_run(
                    id=uuid4(),
                    parent_run_id=parent_id,
                    name=f"incidentops.{name}",
                    run_type="chain",
                    inputs={},
                    outputs={"latency_ms": round(latency, 3)},
                    project_name=self.project,
                    start_time=cursor,
                    end_time=ended,
                )
                cursor = ended
        except Exception:
            # Export failures do not change the assessment or log provider payloads.
            logger.warning("IncidentOps trace export unavailable")
