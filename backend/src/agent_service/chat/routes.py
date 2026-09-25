"""Disconnects close text generation; managed calls have an independent lifetime."""

import asyncio
import json
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agent_service.calls.tools import CallContext
from agent_service.chat.history import model_history
from agent_service.chat.runtime import stream_reply
from agent_service.chat.schemas import ChatRequest
from agent_service.session import check_mutation, require_owner
from agent_service.settings import SettingsError, load_settings

router = APIRouter()
MAX_BODY = 512 * 1024
HEARTBEAT_SECONDS = 15
CHAT_TIMEOUT_SECONDS = 120
ERRORS = {
    "not_configured": ("서버의 모델 설정을 확인해 주세요.", False),
    "invalid_request": (
        "메시지 형식이나 길이를 확인해 주세요. 대화가 길면 새 대화를 시작해 주세요.",
        False,
    ),
    "provider_auth": ("모델 인증에 실패했습니다. 서버 설정을 확인해 주세요.", False),
    "rate_limited": ("모델이 잠시 사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.", True),
    "provider_unavailable": ("응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.", True),
    "timeout": ("응답 시간이 초과되었습니다. 다시 시도해 주세요.", True),
    "storage_unavailable": ("대화를 저장하지 못했습니다. 다시 불러와 확인해 주세요.", True),
    "invalid_stream": ("응답이 정상적으로 완료되지 않았습니다. 다시 시도해 주세요.", True),
}


def error_data(code):
    message, retryable = ERRORS[code]
    return {"code": code, "message": message, "retryable": retryable}


def error_code(error):
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return "provider_auth"
    if isinstance(error, RateLimitError):
        return "rate_limited"
    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "timeout"
    return "provider_unavailable"


async def durable_call(function, *args):
    # An asyncio cancellation must not leave a DB mutation running unobserved.
    task = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class SavedReply:
    def __init__(self, store, owner, identity, settings, call_context=None):
        self.call_context = call_context
        self.store, self.owner, self.identity, self.settings = store, owner, identity, settings
        self.text = ""

    async def save(self, status="streaming", code=None):
        await durable_call(self.store.save_run, self.owner, self.identity, self.text, status, code)

    async def close(self):
        # Also covers disconnect before the generator's first iteration.
        await self.save("stopped")

    async def events(self):
        identity = {k: v for k, v in self.identity.items() if k != "history"}

        def event(name, **values):
            data = json.dumps(identity | values, ensure_ascii=False)
            return f"event: {name}\ndata: {data}\n\n"

        iterator = stream_reply(
            model_history(self.identity["history"]), self.settings, self.call_context
        )
        pending = None
        saved_at = time.monotonic()
        try:
            yield event("start")
            async with asyncio.timeout(CHAT_TIMEOUT_SECONDS):
                while True:
                    pending = asyncio.create_task(anext(iterator))
                    while not (await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS))[0]:
                        yield ": keep-alive\n\n"
                    try:
                        text = pending.result()
                    except StopAsyncIteration:
                        break
                    if text:
                        self.text += text
                        if len(self.text) > 1024 * 1024:
                            raise ValueError("Response too long")
                        if time.monotonic() - saved_at >= 1:
                            await self.save()
                            saved_at = time.monotonic()
                        yield event("delta", text=text)
            if self.text.strip():
                await self.save("completed")
                yield event("done")
            else:
                await self.save("failed", "invalid_stream")
                yield event("error", **error_data("invalid_stream"))
        except Exception as error:
            code = "storage_unavailable" if isinstance(error, sqlite3.Error) else error_code(error)
            try:
                await self.save("failed", code)
            except sqlite3.Error:
                code = "storage_unavailable"
            yield event("error", **error_data(code))
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await iterator.aclose()


class ChatStreamingResponse(StreamingResponse):
    """Monitor disconnect even while the provider is silent (ASGI 2.4+)."""

    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        streaming = asyncio.create_task(self.stream_response(send))
        disconnected = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            await asyncio.wait({streaming, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if streaming.done():
                try:
                    streaming.result()
                except OSError:
                    pass  # The peer can disconnect during the send itself.
        finally:
            streaming.cancel()
            disconnected.cancel()
            await asyncio.gather(streaming, disconnected, return_exceptions=True)
            try:
                await self.body_iterator.aclose()
            finally:
                try:
                    await self.cleanup()
                except sqlite3.Error:
                    pass  # Still-streaming rows become interrupted on the next startup.


@router.post("/api/chat")
async def chat(request: Request):
    check_mutation(request)
    owner = await require_owner(request)
    # Bound manually so rejected data is never echoed by framework validation.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return JSONResponse({"error": error_data("invalid_request")}, status_code=422)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            return JSONResponse({"error": error_data("invalid_request")}, status_code=413)
    try:
        body = ChatRequest.model_validate_json(raw)
    except (ValidationError, ValueError):
        return JSONResponse({"error": error_data("invalid_request")}, status_code=422)
    try:
        settings = load_settings()
    except SettingsError:
        return JSONResponse({"error": error_data("not_configured")}, status_code=503)
    store = request.app.state.store
    reservation = asyncio.create_task(run_in_threadpool(store.begin_run, owner, body))
    try:
        identity = await asyncio.shield(reservation)
    except asyncio.CancelledError:
        identity = await reservation
        await durable_call(store.save_run, owner, identity, "", "stopped")
        raise
    reply = SavedReply(
        store,
        owner,
        identity,
        settings,
        CallContext(
            request.app.state.calls, owner, identity["conversation_id"], identity["user_message_id"]
        ),
    )
    return ChatStreamingResponse(
        reply.events(),
        cleanup=reply.close,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
