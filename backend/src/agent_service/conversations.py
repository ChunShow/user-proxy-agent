from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from agent_service.session import check_mutation, require_owner

router = APIRouter()


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID


@router.get("/api/conversations")
async def conversations(request: Request, cursor: str | None = Query(None, max_length=512)):
    owner = await require_owner(request)
    data = await run_in_threadpool(request.app.state.store.list_conversations, owner, cursor)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/conversations")
async def create(request: Request, body: NewConversation):
    check_mutation(request)
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.create_conversation, owner, str(body.conversation_id)
    )
    return JSONResponse(data, status_code=201, headers={"Cache-Control": "no-store"})


@router.get("/api/conversations/{conversation_id}")
async def conversation(
    request: Request,
    conversation_id: UUID,
    before: int | None = Query(None, ge=1, le=9223372036854775807),
):
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.get_conversation, owner, str(conversation_id), before
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})
