"""Local service entry point. Startup does not contact external providers."""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent_service.chat.routes import router
from agent_service.session import router as session_router
from agent_service.storage import ConversationStore


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["agent-service"] = "agent-service"


def create_app(*, database_path: Path | None = None) -> FastAPI:
    store = ConversationStore(
        database_path or Path(__file__).resolve().parents[3] / "data/agent-service.sqlite3"
    )

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        yield

    app = FastAPI(title="user proxy agent", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.include_router(session_router)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error):
        return JSONResponse(
            {"error": {"code": error.detail}},
            status_code=error.status_code,
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error):
        return JSONResponse({"error": {"code": "invalid_request"}}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error):
        return JSONResponse(
            {"error": {"code": "storage_unavailable"}},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
