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
async def test_http_chat_disconnect_and_text_retry_keep_one_managed_call(monkeypatch, tmp_path):
    from test_call_settings import values
    from test_call_store import spec
    from test_manager import FakeGateway
    from test_native_audio import until

    from agent_service.calls.settings import CallSettings
    from agent_service.calls.tools import build_call_tools
    from agent_service.chat import routes

    gateway = FakeGateway()
    app = create_app(database_path=tmp_path / "db.sqlite3")
    config = CallSettings.load(tmp_path / "missing", environ=values())
    app.state.calls.settings_loader = lambda: config
    app.state.calls.gateway_factory = gateway.open
    app.state.calls.poll_seconds = 0.005
    closed = asyncio.Event()

    async def reply(messages, settings, call_context):
        try:
            await build_call_tools(call_context)[0].ainvoke(spec().model_dump())
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
        uvicorn.Config(
            app,
            log_level="critical",
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(2):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False) as client:
            base = f"http://127.0.0.1:{port}"
            cid = str(uuid4())
            await client.post(base + "/api/session", json={})
            await client.post(base + "/api/conversations", json={"conversation_id": cid})
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{port}/api/chat",
                json={
                    "request_id": str(uuid4()),
                    "conversation_id": cid,
                    "content": "01000000001로 전화해서 테스트 가능한지 물어봐 줘",
                },
            ) as response:
                async for line in response.aiter_lines():
                    if line == "event: delta":
                        break
            await asyncio.wait_for(closed.wait(), timeout=1)
            async with asyncio.timeout(1):
                while True:
                    saved = (await client.get(base + f"/api/conversations/{cid}")).json()[
                        "messages"
                    ][-1]
                    if saved["status"] == "stopped":
                        assert saved["text"] == "첫 응답"
                        break
                    await asyncio.sleep(0.01)
            active = (await client.get(base + "/api/calls/active")).json()["items"]
            assert len(active) == 1 and gateway.dials == 0
            response = await client.post(base + f"/api/calls/{active[0]['id']}/approve",
                                         json={"expected_version": active[0]["version"]})
            assert response.status_code == 200
            await until(lambda: gateway.dials == 1)
            assert not gateway.closed
            async with client.stream(
                "POST",
                base + "/api/chat",
                json={
                    "request_id": str(uuid4()),
                    "conversation_id": cid,
                    "retry_message_id": saved["id"],
                },
            ) as response:
                async for line in response.aiter_lines():
                    if line == "event: delta":
                        break
            assert gateway.dials == 1
            await client.post(base + f"/api/calls/{active[0]['id']}/stop", json={})
            await app.state.calls.wait_idle()
            result = (await client.get(base + f"/api/calls/{active[0]['id']}")).json()
            assert result["status"] == "ended" and gateway.hangups == 1
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=3)
        sock.close()
