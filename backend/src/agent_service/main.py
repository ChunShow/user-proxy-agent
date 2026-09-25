"""Local service entry point. Startup does not contact external providers."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from agent_service.chat.routes import router


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["agent-service"] = "agent-service"


def create_app() -> FastAPI:
    app = FastAPI(title="user proxy agent", version="0.1.0")
    app.include_router(router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
