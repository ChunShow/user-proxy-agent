import base64

import pytest


@pytest.mark.asyncio
async def test_slow_listener_is_dropped_without_blocking_call():
    from agent_service.calls.listening import AudioHub

    hub = AudioHub(queue_size=2)
    slow = hub.subscribe()
    fast = hub.subscribe()
    for _ in range(3):
        hub.publish("caller", base64.b64encode(b"\xff" * 160).decode())
        assert (await fast.get())["track"] == "caller"
    assert await slow.get() == {"type": "closed", "reason": "slow_listener"}
    assert len(hub.listeners) == 1
    hub.close()
    assert (await fast.get())["type"] == "closed"
    assert not hub.listeners


@pytest.mark.asyncio
async def test_media_observer_preserves_carrier_payloads_and_clear():
    from agent_service.calls.listening import AudioHub, ObservedMedia

    raw = {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}

    class Media:
        sent = []

        async def events(self):
            yield raw

        async def send(self, value):
            self.sent.append(value)

        async def send_dtmf(self, digit):
            self.sent.append(digit)

    source, hub = Media(), AudioHub()
    observed = ObservedMedia(source, hub)
    listener = hub.subscribe()
    assert [e async for e in observed.events()] == [raw]
    assert (await listener.get())["track"] == "caller"
    await observed.send(raw)
    assert (await listener.get())["track"] == "assistant"
    await observed.send({"event": "clear"})
    assert await listener.get() == {"type": "clear", "track": "assistant"}
    await observed.send_dtmf("4")
    assert source.sent == [raw, {"event": "clear"}, "4"]
    hub.unsubscribe(listener)
    hub.publish("caller", "invalid ignored when no listeners")
    assert listener.empty()


def test_websocket_checks_owner_origin_and_closes_without_hanging_up(tmp_path):
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from agent_service.calls.listening import AudioHub
    from agent_service.main import create_app
    from agent_service.storage import StoreError

    app = create_app(database_path=tmp_path / "db.sqlite3")
    cid, other = str(uuid4()), str(uuid4())
    hub = AudioHub()
    app.state.calls.audio_hubs[cid] = hub

    async def owned(owner, identity):
        if identity != cid:
            raise StoreError("not_found", 404)
        return {"status": "connected", "stop_requested": False}

    app.state.calls.get = owned
    with TestClient(app) as client:
        origin = {"Origin": "http://127.0.0.1:5180"}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/calls/{cid}/listen", headers=origin):
                pass
        client.post("/api/session", json={})
        for path, headers in [(cid, {}), (cid, {"Origin": "https://evil.test"}), (other, origin)]:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/api/calls/{path}/listen", headers=headers):
                    pass
        with client.websocket_connect(f"/api/calls/{cid}/listen", headers=origin) as ws:
            assert ws.receive_json() == {"type": "ready", "encoding": "mulaw", "sample_rate": 8000}
            assert len(hub.listeners) == 1
            client.portal.call(hub.publish, "caller", base64.b64encode(b"\xff" * 160).decode())
            assert ws.receive_json()["track"] == "caller"
            client.portal.call(hub.close)
            assert ws.receive_json()["reason"] == "call_ended"
        assert not hub.listeners
