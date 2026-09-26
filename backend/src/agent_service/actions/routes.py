from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_service.session import check_mutation, require_owner

router = APIRouter()


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)


def response(value):
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.get("/api/conversations/{conversation_id}/actions")
async def list_actions(conversation_id: UUID, request: Request, owner=Depends(require_owner)):
    manager = request.app.state.actions
    return response({"items": await manager.db(manager.store.list, owner, str(conversation_id))})


@router.post("/api/actions/{action_id}/approve", dependencies=[Depends(check_mutation)])
async def approve(action_id: UUID, body: Decision, request: Request, owner=Depends(require_owner)):
    return response(
        await request.app.state.actions.approve(owner, str(action_id), body.expected_version)
    )


@router.post("/api/actions/{action_id}/reject", dependencies=[Depends(check_mutation)])
async def reject(action_id: UUID, body: Decision, request: Request, owner=Depends(require_owner)):
    manager = request.app.state.actions
    return response(
        await manager.db(manager.store.reject, owner, str(action_id), body.expected_version)
    )
