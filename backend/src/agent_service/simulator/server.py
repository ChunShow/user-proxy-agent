"""Independent loopback-only ARS HTTP/WebSocket service. No model or carrier imports."""

import asyncio
import base64
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from agent_service.simulator.engine import NUMBER, SCENARIO_TEXTS, Line, Playback


class DialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    destination: str


def create_app(*, audio_dir=None, token=None, menu_timeout=20, max_seconds=180):
    directory = Path(audio_dir or os.environ.get("SIMULATOR_AUDIO_DIR", "var/simulator/audio"))
    secret = token or os.environ.get("SIMULATOR_TOKEN", "")
    if len(secret) < 24:
        raise ValueError("SIMULATOR_TOKEN must contain at least 24 characters")
    clips = {}
    lines = {}

    @asynccontextmanager
    async def lifespan(app):
        for key in SCENARIO_TEXTS:
            raw = (directory / f"{key}.ulaw").read_bytes()
            if not raw or len(raw) > 8000 * 60:
                raise ValueError("Invalid simulator audio fixture")
            clips[key] = raw
        try:
            yield
        finally:
            for line in lines.values():
                line.end("server_shutdown")

    app = FastAPI(title="Virtual ARS", lifespan=lifespan, docs_url=None, redoc_url=None)

    def authorized(value):
        return hmac.compare_digest((value or "").encode(), f"Bearer {secret}".encode())

    def auth(authorization: str | None = Header(default=None)):
        if not authorized(authorization):
            raise HTTPException(401, "simulator_auth_required")

    def find(call_id):
        line = lines.get(call_id)
        if line is None:
            raise HTTPException(404, "simulation_not_found")
        if line.status == "active" and time.monotonic() - line.created > max_seconds:
            line.end("time_limit")
        return line

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "virtual-ars", "real_calls": False}

    @app.get("/scenarios", dependencies=[Depends(auth)])
    async def scenarios():
        return {"items": [{"id": "clinic-hours", "number": NUMBER, "name": "가상 병원 진료 시간"}]}

    @app.post("/calls", dependencies=[Depends(auth)])
    async def dial(body: DialBody):
        if body.destination != NUMBER:
            raise HTTPException(422, "simulation_number_unknown")
        identity = "SIM" + body.request_id.hex
        if identity not in lines:
            for key, line in list(lines.items()):
                if time.monotonic() - line.created > 900:
                    line.end("expired")
                    del lines[key]
            if len(lines) >= 100:
                raise HTTPException(503, "simulation_capacity")
            line = Line(identity, body.destination)
            line.record("created")
            lines[identity] = line
        return find(identity).snapshot()

    @app.get("/calls/{call_id}", dependencies=[Depends(auth)])
    async def lookup(call_id: str):
        return find(call_id).snapshot()

    @app.post("/calls/{call_id}/hangup", dependencies=[Depends(auth)])
    async def hangup(call_id: str):
        line = find(call_id)
        line.end("hangup_requested")
        return line.snapshot()

    @app.websocket("/calls/{call_id}/media")
    async def media(ws: WebSocket, call_id: str):
        if not authorized(ws.headers.get("authorization")):
            await ws.close(code=1008)
            return
        try:
            line = find(call_id)
        except HTTPException:
            await ws.close(code=1008)
            return
        if line.status != "active" or line.connected:
            await ws.close(code=1008)
            return
        line.connected = True
        await ws.accept()
        lock = asyncio.Lock()

        async def send(event):
            async with lock:
                await asyncio.wait_for(ws.send_json(event), 3)

        player = Playback(send)
        line.player = player
        await send(
            {
                "event": "start",
                "start": {
                    "accountId": "simulation",
                    "callId": call_id,
                    "mediaFormat": {"sampleRate": 8000, "channels": 1, "encoding": "audio/x-mulaw"},
                },
            }
        )
        line.record("media_connected")

        async def input_audio():
            revision, raw, offset, finished = -1, b"", 0, None
            deadline = time.monotonic()
            while not line.closed.is_set():
                if time.monotonic() - line.created >= max_seconds:
                    line.end("time_limit")
                    break
                if revision != line.revision:
                    revision = line.revision
                    raw = (clips["invalid"] if line.replay_invalid else b"") + clips[line.stage]
                    offset, finished = 0, None
                    line.record("prompt_started", stage=line.stage)
                packet = raw[offset : offset + 160]
                offset += len(packet)
                await send(
                    {
                        "event": "media",
                        "media": {
                            "payload": base64.b64encode(packet.ljust(160, b"\xff")).decode(),
                        },
                    }
                )
                deadline += 0.02
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                if revision == line.revision and offset >= len(raw) and finished is None:
                    finished = time.monotonic()
                    line.record("prompt_delivered", stage=line.stage)
                    if line.stage == "hours":
                        line.hours_delivered = True
                if (
                    finished is not None
                    and line.stage != "hours"
                    and time.monotonic() - finished > menu_timeout
                ):
                    if line.repeats >= 2:
                        line.end("no_input")
                    else:
                        line.repeats += 1
                        line.replay_invalid = False
                        line.revision += 1
            await send({"event": "stop", "stop": {"accountId": "simulation", "callId": call_id}})

        async def receive():
            while not line.closed.is_set():
                event = await ws.receive_json()
                if not isinstance(event, dict):
                    raise ValueError("invalid_event")
                kind = event.get("event")
                if kind == "media":
                    raw = base64.b64decode(event["media"]["payload"], validate=True)
                    if not 0 < len(raw) <= 8000:
                        raise ValueError("invalid_audio")
                    player.media(raw)
                elif kind == "mark":
                    name = event["mark"]["name"]
                    if not isinstance(name, str) or len(name) > 160:
                        raise ValueError("invalid_mark")
                    player.mark(name)
                elif kind == "clear":
                    player.clear()
                    line.record("playback_cleared")
                elif kind == "dtmf":
                    digit = event["dtmf"]["digit"]
                    if not isinstance(digit, str) or digit not in "0123456789*#" or len(digit) != 1:
                        raise ValueError("invalid_dtmf")
                    line.digit(digit)
                else:
                    raise ValueError("unknown_event")

        tasks = [asyncio.create_task(fn()) for fn in (input_audio, receive, player.run)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            # Closing media is independent of carrier hangup. The application's
            # cleanup path closes this socket before its hangup API request.
            pass
        except (
            ValueError,
            KeyError,
            TypeError,
            asyncio.QueueFull,
            RuntimeError,
            TimeoutError,
        ):
            line.end("media_closed")
        finally:
            line.record("media_disconnected")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await ws.close()
            except RuntimeError:
                pass

    return app
