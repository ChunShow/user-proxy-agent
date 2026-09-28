from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agent_service.session import check_mutation, require_owner

router = APIRouter()


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    mode: Literal["real", "simulation"] = "real"


@router.get("/api/conversations")
async def conversations(
    request: Request, cursor: str | None = Query(None, max_length=512), deleted: bool = False
):
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.list_conversations, owner, cursor, deleted
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/conversations")
async def create(request: Request, body: NewConversation):
    check_mutation(request)
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.create_conversation, owner, str(body.conversation_id), body.mode
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


class RenameConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)


@router.post("/api/conversations/{conversation_id}/rename", dependencies=[Depends(check_mutation)])
async def rename(request: Request, conversation_id: UUID, body: RenameConversation):
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.rename_conversation, owner, str(conversation_id), body.title
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/conversations/{conversation_id}/delete", dependencies=[Depends(check_mutation)])
async def delete(request: Request, conversation_id: UUID):
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.delete_conversation, owner, str(conversation_id)
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/conversations/{conversation_id}/restore", dependencies=[Depends(check_mutation)])
async def restore(request: Request, conversation_id: UUID):
    owner = await require_owner(request)
    data = await run_in_threadpool(
        request.app.state.store.restore_conversation, owner, str(conversation_id)
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/api/conversations/{conversation_id}/title", dependencies=[Depends(check_mutation)])
async def title(request: Request, conversation_id: UUID):
    from agent_service.chat.titles import update_title

    owner = await require_owner(request)
    await update_title(request.app.state.store, owner, str(conversation_id))
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
