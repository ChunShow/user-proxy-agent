"""Local service entry point. Startup only recovers unfinished calls for cleanup."""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent_service.actions.manager import ActionManager
from agent_service.actions.routes import router as actions_router
from agent_service.calls.listening import router as listening_router
from agent_service.calls.manager import CallManager
from agent_service.calls.reports import CallReportManager
from agent_service.calls.routes import router as calls_router
from agent_service.calls.store import CallStore
from agent_service.chat.routes import router
from agent_service.conversations import router as conversations_router
from agent_service.integrations.google import GoogleManager
from agent_service.integrations.logging import install_access_filter
from agent_service.integrations.routes import router as integrations_router
from agent_service.observability import shutdown_tracing
from agent_service.session import router as session_router
from agent_service.storage import ConversationStore, StoreError


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["agent-service"] = "agent-service"


def create_app(*, database_path: Path | None = None) -> FastAPI:
    install_access_filter()
    store = ConversationStore(
        database_path
        or Path(
            os.environ.get(
                "AGENT_SERVICE_DATABASE_PATH",
                str(Path(__file__).resolve().parents[3] / "data/agent-service.sqlite3"),
            )
        )
    )

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        await run_in_threadpool(app.state.actions.store.recover)
        await app.state.calls.recover()
        await app.state.call_reports.start()
        try:
            yield
        finally:
            await app.state.actions.shutdown()
            await app.state.calls.shutdown()
            await app.state.call_reports.shutdown()
            await run_in_threadpool(shutdown_tracing)

    app = FastAPI(title="user proxy agent", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.calls = CallManager(CallStore(store))
    app.state.integrations = GoogleManager(store)
    app.state.calls.integrations = app.state.integrations
    app.state.actions = ActionManager(store, app.state.integrations)
    app.state.calls.actions = app.state.actions
    app.state.call_reports = CallReportManager(app.state.calls.store)
    app.include_router(listening_router)
    app.include_router(actions_router)
    app.include_router(integrations_router)
    app.include_router(calls_router)
    app.include_router(session_router)
    app.include_router(conversations_router)

    @app.exception_handler(StoreError)
    async def store_error(request: Request, error):
        return JSONResponse(
            {"error": {"code": error.code, **error.details}},
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

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
