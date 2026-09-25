"""One request owns one stream. Disconnects cancel and close all upstream work."""

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError
from pydantic import ValidationError

from agent_service.chat.runtime import stream_reply
from agent_service.chat.schemas import ChatRequest
from agent_service.settings import SettingsError, load_settings

router = APIRouter()
MAX_BODY = 512 * 1024
HEARTBEAT_SECONDS = 15
CHAT_TIMEOUT_SECONDS = 120
ERRORS = {
    "not_configured": ("서버의 모델 설정을 확인해 주세요.", False),
    "invalid_request": (
        "메시지 형식이나 길이를 확인해 주세요. 대화가 길면 새로고침해 주세요.",
        False,
    ),
    "provider_auth": ("모델 인증에 실패했습니다. 서버 설정을 확인해 주세요.", False),
    "rate_limited": ("모델이 잠시 사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.", True),
    "provider_unavailable": ("응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.", True),
    "timeout": ("응답 시간이 초과되었습니다. 다시 시도해 주세요.", True),
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


async def reply_events(body, settings):
    identity = {"request_id": str(body.request_id), "message_id": str(uuid4())}

    def event(name, **values):
        data = json.dumps(identity | values, ensure_ascii=False)
        return f"event: {name}\ndata: {data}\n\n"

    yield event("start")
    iterator = stream_reply([m.model_dump() for m in body.messages], settings)
    pending = None
    has_text = False
    try:
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
                    has_text = has_text or bool(text.strip())
                    yield event("delta", text=text)
        yield event("done") if has_text else event("error", **error_data("invalid_stream"))
    except Exception as error:
        yield event("error", **error_data(error_code(error)))
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


class ChatStreamingResponse(StreamingResponse):
    """Monitor disconnect even while the provider is silent (ASGI 2.4+)."""

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
            await self.body_iterator.aclose()


@router.post("/api/chat")
async def chat(request: Request):
    # JSON-only prevents cross-origin simple form POSTs to this local service.
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
    return ChatStreamingResponse(
        reply_events(body, settings),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
