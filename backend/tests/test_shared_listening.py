"""Exercise HTTP auth plus real WebSocket upgrades through the shipping Caddy template."""

import asyncio
import base64
import json
import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_shared_listening_auth_preserves_audio_websocket(tmp_path):
    caddy = shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy is required for the sharing gateway integration test")
    upstream, front = free_port(), free_port()
    while front == upstream:
        front = free_port()
    app = FastAPI()
    checks = []
    tracks = [
        dict(type="audio", track=track, payload=base64.b64encode(b"\xaa" * 160).decode())
        for track in ["caller", "assistant"]
    ]

    @app.get("/_access/verify")
    async def verify(request: Request):
        checks.append(dict(request.headers))
        return Response(status_code=204 if request.cookies.get("test_access") == "ok" else 401)

    @app.websocket("/api/calls/test/listen")
    async def listen(ws: WebSocket):
        assert ws.headers["origin"] == "http://127.0.0.1:5180"
        await ws.accept()
        await ws.send_json({"type": "ready", "encoding": "mulaw", "sample_rate": 8000})
        for track in tracks:
            await ws.send_json(track)
        await ws.close()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=upstream, log_level="error", lifespan="off")
    )
    server_task = asyncio.create_task(server.serve())
    process = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        template = Path(__file__).resolve().parents[2] / "config/Caddyfile"
        config = template.read_text().replace("http://:5188", f"http://:{front}")
        config = config.replace("127.0.0.1:5189", f"127.0.0.1:{upstream}")
        config = config.replace("127.0.0.1:9010", f"127.0.0.1:{upstream}")
        path = tmp_path / "Caddyfile"
        path.write_text(config)
        process = await asyncio.create_subprocess_exec(
            caddy,
            "run",
            "--config",
            str(path),
            "--adapter",
            "caddyfile",
            env={
                **os.environ,
                "UPA_PUBLIC_ORIGIN": "https://share.example",
                "UPA_WEB_ROOT": str(tmp_path),
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    await client.get(f"http://127.0.0.1:{front}/", timeout=0.2)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.02)
            else:
                pytest.fail("Caddy did not start")
        url = f"ws://127.0.0.1:{front}/api/calls/test/listen"
        with pytest.raises(InvalidStatus):
            async with connect(url, origin="https://share.example", proxy=None):
                pytest.fail("anonymous listener admitted")
        with pytest.raises(InvalidStatus):
            async with connect(
                url,
                origin="https://evil.example",
                proxy=None,
                additional_headers={"Cookie": "test_access=ok"},
            ):
                pytest.fail("foreign origin admitted")
        async with connect(
            url,
            origin="https://share.example",
            proxy=None,
            additional_headers={"Cookie": "test_access=ok"},
        ) as ws:
            assert json.loads(await ws.recv())["type"] == "ready"
            received = [json.loads(await ws.recv()), json.loads(await ws.recv())]
            assert received == tracks
        assert checks and all("upgrade" not in h for h in checks)
        assert all(h.get("connection", "").lower() != "upgrade" for h in checks)
    finally:
        if process and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        server.should_exit = True
        await asyncio.wait_for(server_task, 5)
