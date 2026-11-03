from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from incidentops.config import Settings
from incidentops.factory import build_service
from incidentops.models import Assessment, Principal, Question
from incidentops.security import authenticate


def create_app(settings: Settings | None = None, *, service=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.rag = service if service is not None else build_service(settings)
        yield

    app = FastAPI(
        title="IncidentOps",
        version="0.1.0",
        lifespan=lifespan,
        description="Increment 1: synthetic, extractive documentation RAG. No live diagnostics.",
    )
    bearer = HTTPBearer(auto_error=False)

    def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        result = authenticate(
            credentials.credentials if credentials else "",
            settings.acme_token,
            settings.bravo_token,
        )
        if result is None:
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
        return result

    @app.get("/healthz")
    def health():
        return {"status": "ok", "increment": 1}

    @app.post("/v1/ask", response_model=Assessment)
    def ask(request: Question, actor: Annotated[Principal, Depends(principal)]):
        # Sync endpoint runs in FastAPI's threadpool, not on the async event loop.
        return app.state.rag.ask(actor, request)

    @app.get("/v1/sources/{chunk_id}")
    def source(chunk_id: str, actor: Annotated[Principal, Depends(principal)]):
        try:
            chunk = app.state.rag.store.get_chunk(actor, chunk_id)
        except Exception as exc:
            raise HTTPException(503, "Source store unavailable") from exc
        if chunk is None:
            # Same result for nonexistent, unauthorized, stale and superseded sources.
            raise HTTPException(404, "Source unavailable")
        return chunk

    return app
