"""Owner-scoped status and immediate control. No HTTP dial bypass."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from agent_service.session import check_mutation, require_owner

router = APIRouter()


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


def response(value):
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.get("/api/calls/active")
async def active(request: Request, owner=Depends(require_owner)):
    manager = request.app.state.calls
    return response({"items": await manager.db(manager.store.active, owner)})


@router.get("/api/conversations/{conversation_id}/calls")
async def list_calls(
    conversation_id: UUID, request: Request, cursor: str | None = None, owner=Depends(require_owner)
):
    manager = request.app.state.calls
    return response(await manager.db(manager.store.list, owner, str(conversation_id), cursor))


@router.get("/api/calls/{call_id}")
async def get_call(call_id: UUID, request: Request, owner=Depends(require_owner)):
    return response(await request.app.state.calls.get(owner, str(call_id)))


@router.post("/api/calls/{call_id}/stop", dependencies=[Depends(check_mutation)])
async def stop_call(call_id: UUID, body: EmptyBody, request: Request, owner=Depends(require_owner)):
    return response(await request.app.state.calls.stop(owner, str(call_id)))


@router.post("/api/calls/{call_id}/refresh", dependencies=[Depends(check_mutation)])
async def refresh_call(
    call_id: UUID, body: EmptyBody, request: Request, owner=Depends(require_owner)
):
    return response(await request.app.state.calls.refresh(owner, str(call_id)))
