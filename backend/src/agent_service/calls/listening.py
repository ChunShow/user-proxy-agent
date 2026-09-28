"""Ephemeral, bounded read-only audio fan-out; never owns the carrier call."""

import asyncio
import os
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from agent_service.session import COOKIE
from agent_service.storage import StoreError

router = APIRouter()


class AudioHub:
    def __init__(self, *, queue_size=100):
        self.listeners = set()
        self.queue_size = queue_size
        self.closed = False

    def subscribe(self):
        if self.closed or len(self.listeners) >= 2:
            raise StoreError("listening_unavailable")
        queue = asyncio.Queue(self.queue_size)
        self.listeners.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.listeners.discard(queue)

    def _close(self, queue, reason):
        self.unsubscribe(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait({"type": "closed", "reason": reason})

    def broadcast(self, event):
        for queue in tuple(self.listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._close(queue, "slow_listener")

    def publish(self, track, payload):
        if self.listeners:
            self.broadcast({"type": "audio", "track": track, "payload": payload})

    def close(self):
        self.closed = True
        for queue in tuple(self.listeners):
            self._close(queue, "call_ended")


class ObservedMedia:
    def __init__(self, media, hub):
        self.media, self.hub = media, hub

    async def events(self):
        async for event in self.media.events():
            if event.get("event") == "media":
                self.hub.publish("caller", event["media"]["payload"])
            yield event

    async def send(self, event):
        await self.media.send(event)
        if event.get("event") == "media":
            self.hub.publish("assistant", event["media"]["payload"])
        elif event.get("event") == "clear":
            self.hub.broadcast({"type": "clear", "track": "assistant"})

    async def send_dtmf(self, digit):
        return await self.media.send_dtmf(digit)


@router.websocket("/api/calls/{call_id}/listen")
async def listen(socket: WebSocket, call_id: UUID):
    ports = {os.getenv("WEB_PORT", "5180"), os.getenv("BACKEND_PORT", "9010")}
    allowed = {f"http://127.0.0.1:{port}" for port in ports}
    if socket.headers.get("origin") not in allowed:
        await socket.close(code=1008)
        return
    manager = socket.app.state.calls
    owner = await run_in_threadpool(
        socket.app.state.store.owner_for_token,
        socket.cookies.get(getattr(socket.app.state, "session_cookie", COOKIE)),
    )
    queue, hub = None, None
    try:
        if owner is None:
            raise StoreError("session_expired")
        row = await manager.get(owner, str(call_id))
        hub = manager.audio_hubs.get(str(call_id))
        if row["status"] != "connected" or row["stop_requested"] or hub is None:
            raise StoreError("listening_unavailable")
        queue = hub.subscribe()
    except StoreError:
        await socket.close(code=1008)
        return
    tasks = []
    try:
        await socket.accept()
        await socket.send_json({"type": "ready", "encoding": "mulaw", "sample_rate": 8000})

        async def send():
            while True:
                event = await queue.get()
                await asyncio.wait_for(socket.send_json(event), 3)
                if event["type"] == "closed":
                    return

        async def receive():
            # Read only. Any client data closes the monitor, never controls the call.
            await socket.receive_text()

        async def guard():
            while True:
                await asyncio.sleep(10)
                current = await run_in_threadpool(
                    socket.app.state.store.owner_for_token,
                    socket.cookies.get(getattr(socket.app.state, "session_cookie", COOKIE)),
                )
                row = await manager.get(owner, str(call_id))
                if current != owner or row["status"] != "connected" or row["stop_requested"]:
                    return

        tasks = [asyncio.create_task(f()) for f in (send, receive, guard)]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await socket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
