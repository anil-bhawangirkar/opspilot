import time
from typing import TypedDict
from uuid import uuid4

from langchain_core.embeddings import Embeddings
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from incidentops.models import (
    Assessment,
    Citation,
    Claim,
    Draft,
    Evidence,
    Metrics,
    Principal,
    Question,
)
from incidentops.providers import PROMPT_VERSION, Generator, render_context
from incidentops.security import redact, token_upper_bound
from incidentops.store import Store
from incidentops.tracing import TraceSink


class RAGState(TypedDict, total=False):
    question: str
    evidence: list[Evidence]
    draft: Draft
    reason: str
    claims: list[Claim]


class RAGService:
    def __init__(
        self,
        store: Store,
        embeddings: Embeddings,
        generator: Generator,
        *,
        mode: str,
        top_k: int,
        context_tokens: int,
        trace_sink: TraceSink,
    ):
        self.store, self.embeddings, self.generator = store, embeddings, generator
        self.mode, self.top_k, self.context_tokens = mode, top_k, context_tokens
        self.trace_sink = trace_sink

    def retrieve(self, principal: Principal, request: Question) -> list[Evidence]:
        vector = self.embeddings.embed_query(redact(request.question))
        return self.store.search(principal, request, vector, self.top_k)

    def ask(self, principal: Principal, request: Question) -> Assessment:
        started = time.perf_counter()
        stages = []

        # Immutable auth context is captured by trusted code, never placed in model-editable state.
        def retrieve(state):
            tick = time.perf_counter()
            try:
                safe_request = request.model_copy(update={"question": state["question"]})
                candidates = self.retrieve(principal, safe_request)
                chosen = []
                for item in candidates:
                    if token_upper_bound(render_context([*chosen, item])) <= self.context_tokens:
                        chosen.append(item)
                return {"evidence": chosen, "reason": "no_evidence" if not chosen else ""}
            except Exception:
                return {"evidence": [], "reason": "retrieval_error"}
            finally:
                stages.append(("retrieve", (time.perf_counter() - tick) * 1000))

        def generate(state):
            tick = time.perf_counter()
            try:
                raw = self.generator.generate(state["question"], state["evidence"])
                draft = Draft.model_validate(raw)
                return {"draft": draft}
            except (ValidationError, TypeError):
                return {"reason": "invalid_output"}
            except Exception:
                return {"reason": "provider_error"}
            finally:
                stages.append(("generate", (time.perf_counter() - tick) * 1000))

        def validate(state):
            tick = time.perf_counter()
            try:
                if state.get("reason"):
                    return {"claims": []}
                draft = state["draft"]
                if not draft.answerable or not draft.claims:
                    return {"reason": "not_answerable", "claims": []}
                authorized = {e.chunk.chunk_id: e.chunk for e in state["evidence"]}
                claims = []
                for proposal in draft.claims:
                    chunk = authorized.get(proposal.evidence_id)
                    # Recheck authorization/freshness immediately before release.
                    current = self.store.get_chunk(principal, proposal.evidence_id)
                    if (
                        chunk is None
                        or current is None
                        or current.model_dump() != chunk.model_dump()
                        or proposal.quote not in chunk.text
                        or proposal.quote != redact(proposal.quote)
                    ):
                        return {"reason": "invalid_grounding", "claims": []}
                    claim = Claim(quote=proposal.quote, citation=Citation.from_chunk(chunk))
                    if claim not in claims:
                        claims.append(claim)
                return {"reason": "verified_quotes", "claims": claims}
            except Exception:
                return {"reason": "invalid_grounding", "claims": []}
            finally:
                stages.append(("validate", (time.perf_counter() - tick) * 1000))

        graph = StateGraph(RAGState)
        graph.add_node("retrieve", retrieve)
        graph.add_node("generate", generate)
        graph.add_node("validate", validate)
        graph.add_edge(START, "retrieve")
        graph.add_conditional_edges("retrieve", lambda s: "validate" if s["reason"] else "generate")
        graph.add_edge("generate", "validate")
        graph.add_edge("validate", END)
        # Disable automatic tracing even when inherited LANGSMITH_TRACING is true.
        # Only the allowlisted summary below may cross the telemetry boundary.
        with tracing_context(enabled=False):
            state = graph.compile().invoke(
                {"question": redact(request.question)},
                config={"recursion_limit": 8},
            )
        evidence = state["evidence"]
        # Citations in retrieval metadata need the same final check as answer claims.
        try:
            still_authorized = all(
                self.store.get_chunk(principal, e.chunk.chunk_id) == e.chunk for e in evidence
            )
        except Exception:
            still_authorized = False
        if not still_authorized:
            evidence = []
            state.update(claims=[], reason="invalid_grounding")
        result = Assessment(
            run_id=str(uuid4()),
            mode=self.mode,
            status="answered" if state["claims"] else "insufficient_evidence",
            reason=state["reason"],
            claims=state["claims"],
            retrieved=[Citation.from_chunk(e.chunk) for e in evidence],
            metrics=Metrics(
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                context_tokens=token_upper_bound(render_context(evidence)) if evidence else 0,
                retrieved_chunks=len(evidence),
                prompt_version=PROMPT_VERSION,
            ),
        )
        self.trace_sink.emit(result, stages)
        return result
