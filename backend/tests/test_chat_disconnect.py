import asyncio
import socket
from uuid import uuid4

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from agent_service.main import create_app
from agent_service.settings import Settings


@pytest.mark.asyncio
async def test_real_http_disconnect_closes_async_upstream_within_one_second(monkeypatch):
    from agent_service.chat import routes

    closed = asyncio.Event()

    async def reply(*args):
        try:
            yield "첫 응답"
            await asyncio.sleep(300)
            yield "늦은 응답"
        finally:
            await asyncio.sleep(0)
            closed.set()

    monkeypatch.setattr(routes, "stream_reply", reply)
    monkeypatch.setattr(
        routes, "load_settings", lambda: Settings("https://model.test", SecretStr("fake"), "test")
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), log_level="critical", timeout_graceful_shutdown=1)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(2):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{port}/api/chat",
                json={
                    "request_id": str(uuid4()),
                    "messages": [{"role": "user", "content": "시작"}],
                },
            ) as response:
                async for line in response.aiter_lines():
                    if line == "event: delta":
                        break
        await asyncio.wait_for(closed.wait(), timeout=1)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=3)
        sock.close()
