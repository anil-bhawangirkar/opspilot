import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from incidentops.durable.config import WorkflowSettings
from incidentops.durable.http_limits import BodyLimitMiddleware
from incidentops.durable.repository import Conflict
from incidentops.durable.runtime import create_runtime
from incidentops.models import Principal, Question, SourceDocument, StrictModel
from incidentops.security import authenticate


class IngestionRequest(StrictModel):
    document: SourceDocument
    expected_revision: int | None = Field(default=None, ge=0)


class DeletionRequest(StrictModel):
    document_id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=160)
    expected_revision: int = Field(ge=0)


def create_workflow_app(settings=None, *, runtime=None, retrieval_only=False):
    settings = settings or WorkflowSettings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = runtime if runtime is not None else await create_runtime(settings)
        try:
            yield
        finally:
            if runtime is None:
                await app.state.runtime.aclose()

    app = FastAPI(title="IncidentOps asynchronous workflows", version="0.2.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    bearer = HTTPBearer(auto_error=False)

    def principal(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        actor = authenticate(
            credentials.credentials if credentials else "",
            settings.acme_token,
            settings.bravo_token,
        )
        if actor is None:
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
        return actor

    Actor = Annotated[Principal, Depends(principal)]
    Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)]

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(
            status_code=409, content={"detail": "Conflicting request or document revision"}
        )

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse(
            status_code=403, content={"detail": "Operation outside principal scope"}
        )

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Workflow persistence unavailable"})

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "api": "v2", "retrieval_only": retrieval_only}

    @app.post("/v2/retrieve")
    async def retrieve(question: Question, actor: Actor):
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                result = await app.state.runtime.retrieve(actor, question)
        except TimeoutError as exc:
            raise HTTPException(504, "Retrieval deadline exceeded") from exc
        return {
            "mode": "bm25+dense-rrf",
            "reranker": "disabled",
            "evidence": [
                {
                    "quote": item.chunk.text,
                    "score": item.score,
                    "citation": app.state.runtime.citation(item.chunk).model_dump(),
                }
                for item in result.evidence
            ]
            if result
            else [],
            "duplicate_sources": result.duplicate_sources if result else {},
        }

    @app.get("/v2/sources/{chunk_id}")
    async def source(chunk_id: str, actor: Actor):
        result = await app.state.runtime.repo.get_chunk(
            actor, chunk_id, embedding_id=app.state.runtime.embedding_id
        )
        if result is None:
            raise HTTPException(404, "Source unavailable")
        return result

    if retrieval_only:
        return app

    async def accepted(actor, job):
        view = await app.state.runtime.job_view(actor, job["id"])
        return JSONResponse(
            status_code=202, content=view, headers={"Location": f"/v2/jobs/{job['id']}"}
        )

    @app.post("/v2/ingestion-jobs", status_code=202)
    async def ingestion(request: IngestionRequest, actor: Actor, key: Key):
        job = await app.state.runtime.submit_document(
            actor, key, request.document, request.expected_revision
        )
        return await accepted(actor, job)

    @app.post("/v2/answer-jobs", status_code=202)
    async def answer(request: Question, actor: Actor, key: Key):
        return await accepted(actor, await app.state.runtime.submit_question(actor, key, request))

    @app.post("/v2/deletion-jobs", status_code=202)
    async def delete(request: DeletionRequest, actor: Actor, key: Key):
        return await accepted(
            actor,
            await app.state.runtime.submit_delete(
                actor, key, request.document_id, request.expected_revision
            ),
        )

    @app.get("/v2/jobs/{job_id}")
    async def job(job_id: str, actor: Actor):
        result = await app.state.runtime.job_view(actor, job_id)
        if result is None:
            raise HTTPException(404, "Job unavailable")
        return result

    @app.post("/v2/jobs/{job_id}/cancel")
    async def cancel(job_id: str, actor: Actor):
        if await app.state.runtime.repo.get_job(actor, job_id) is None:
            raise HTTPException(404, "Job unavailable")
        return {"cancelled": await app.state.runtime.repo.cancel(actor, job_id)}

    @app.get("/v2/jobs/{job_id}/events")
    async def stream(job_id: str, actor: Actor, after: int = 0):
        if after < 0:
            raise HTTPException(422, "Cursor must be non-negative")
        if await app.state.runtime.job_view(actor, job_id) is None:
            raise HTTPException(404, "Job unavailable")

        async def records():
            cursor, until = after, time.monotonic() + 30
            while time.monotonic() < until:
                rows = await app.state.runtime.repo.job_events(actor, job_id, cursor)
                if rows is None:
                    return
                for row in rows:
                    cursor = row["sequence"]
                    yield (
                        f"id: {cursor}\nevent: {row['event_type']}\n"
                        f"data: {json.dumps(row['payload'])}\n\n"
                    )
                view = await app.state.runtime.job_view(actor, job_id)
                if view is None:
                    return
                if (
                    view["status"]
                    in {
                        "succeeded",
                        "failed",
                        "expired",
                        "cancelled",
                    }
                    and cursor >= view["event_sequence"]
                ):
                    return
                if len(rows) == 100 or (
                    view["status"] in {"succeeded", "failed", "expired", "cancelled"}
                    and cursor < view["event_sequence"]
                ):
                    continue
                await asyncio.sleep(0.25)

        return StreamingResponse(records(), media_type="text/event-stream")

    return app
