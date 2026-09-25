"""Owner-scoped status and immediate control. No HTTP dial bypass."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_service.calls.live_store import LiveStore
from agent_service.session import check_mutation, require_owner

router = APIRouter()


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)
    request_id: UUID


@router.get("/api/calls/{call_id}/activity")
async def activity(call_id: UUID, request: Request, after: int = 0, owner=Depends(require_owner)):
    manager = request.app.state.calls
    return response(await manager.db(LiveStore(manager.store).activity, owner, str(call_id), after))


@router.post(
    "/api/calls/{call_id}/confirmations/{question_id}/answer",
    dependencies=[Depends(check_mutation)],
)
async def answer(
    call_id: UUID,
    question_id: UUID,
    body: AnswerBody,
    request: Request,
    owner=Depends(require_owner),
):
    manager = request.app.state.calls
    result = await manager.db(
        LiveStore(manager.store).answer,
        owner,
        str(call_id),
        str(question_id),
        body.answer,
        body.expected_revision,
        str(body.request_id),
    )
    return response(result)


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
