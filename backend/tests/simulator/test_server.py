import asyncio
import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.simulator.engine import SCENARIO_TEXTS, Playback
from agent_service.simulator.server import create_app

TOKEN = "local-test-token-for-simulator"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def app(tmp_path):
    for key in SCENARIO_TEXTS:
        (tmp_path / f"{key}.ulaw").write_bytes(b"\xff" * 320)
    return create_app(audio_dir=tmp_path, token=TOKEN)


def test_auth_number_validation_and_idempotent_call(tmp_path):
    with TestClient(app(tmp_path)) as c:
        assert c.post("/calls", json={}).status_code == 401
        body = {"request_id": str(uuid4()), "destination": "01000000001"}
        assert (
            c.post(
                "/calls", json=body | {"destination": "01000000002"}, headers=HEADERS
            ).status_code
            == 422
        )
        a = c.post("/calls", json=body, headers=HEADERS).json()
        assert c.post("/calls", json=body, headers=HEADERS).json()["id"] == a["id"]
        assert a["scenario"] == "clinic-hours" and a["status"] == "active"
        assert c.get(f"/calls/{a['id']}").status_code == 401
        assert c.post(f"/calls/{a['id']}/hangup", headers=HEADERS).json()["status"] == "completed"


def test_websocket_audio_dtmf_and_hangup(tmp_path):
    with TestClient(app(tmp_path)) as c:
        a = c.post(
            "/calls",
            json={"request_id": str(uuid4()), "destination": "01000000001"},
            headers=HEADERS,
        ).json()
        path = f"/calls/{a['id']}"
        with c.websocket_connect(path + "/media", headers=HEADERS) as ws:
            assert ws.receive_json()["event"] == "start"
            assert len(base64.b64decode(ws.receive_json()["media"]["payload"])) == 160
            for digit in ["9", "4", "2"]:
                ws.send_json({"event": "dtmf", "dtmf": {"digit": digit}})
                for _ in range(4):
                    ws.receive_json()
            state = c.get(path, headers=HEADERS).json()
            assert state["stage"] == "hours"
            assert state["digits"] == ["9", "4", "2"]
            assert state["invalid_digits"] == 1
            assert state["hours_delivered"]
            c.post(path + "/hangup", headers=HEADERS)
            for _ in range(20):
                if ws.receive_json()["event"] == "stop":
                    break
            else:
                pytest.fail("missing stop")


@pytest.mark.asyncio
async def test_playback_mark_waits_for_audio_and_clear_discards_pending_marks():
    sent = []

    async def send(event):
        sent.append(event)

    player = Playback(send)
    worker = asyncio.create_task(player.run())
    try:
        player.media(b"\xff" * 1600)
        player.mark("one")
        await asyncio.sleep(0.04)
        assert not sent
        await asyncio.sleep(0.2)
        assert sent == [{"event": "mark", "mark": {"name": "one"}}]
        player.media(b"\xff" * 8000)
        player.mark("discard")
        await asyncio.sleep(0.02)
        player.clear()
        player.mark("after-clear")
        await asyncio.sleep(0.04)
        assert [e["mark"]["name"] for e in sent] == ["one", "after-clear"]
        assert player.played_bytes == 1600
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def test_websocket_auth_single_connection_and_no_input_timeout(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    for key in SCENARIO_TEXTS:
        (tmp_path / f"{key}.ulaw").write_bytes(b"\xff" * 160)
    service = create_app(audio_dir=tmp_path, token=TOKEN, menu_timeout=0.03)
    with TestClient(service) as c:
        a = c.post(
            "/calls",
            json={"request_id": str(uuid4()), "destination": "01000000001"},
            headers=HEADERS,
        ).json()
        path = f"/calls/{a['id']}"
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(path + "/media"):
                pytest.fail("unauthenticated stream accepted")
        with c.websocket_connect(path + "/media", headers=HEADERS) as ws:
            assert ws.receive_json()["event"] == "start"
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(path + "/media", headers=HEADERS):
                    pytest.fail("duplicate stream accepted")
            for _ in range(50):
                if ws.receive_json()["event"] == "stop":
                    break
            else:
                pytest.fail("no-input timeout did not stop")
        state = c.get(path, headers=HEADERS).json()
        assert state["end_reason"] == "no_input"
        assert not state["hours_delivered"]
        assert sum(e["event"] == "prompt_delivered" for e in state["events"]) == 3


def test_malformed_media_closes_line(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app(tmp_path)) as c:
        a = c.post(
            "/calls",
            json={"request_id": str(uuid4()), "destination": "01000000001"},
            headers=HEADERS,
        ).json()
        path = f"/calls/{a['id']}"
        with c.websocket_connect(path + "/media", headers=HEADERS) as ws:
            ws.receive_json()
            ws.send_json([])
            with pytest.raises(WebSocketDisconnect):
                for _ in range(20):
                    ws.receive_json()
        assert c.get(path, headers=HEADERS).json()["end_reason"] == "media_closed"


def test_media_disconnect_does_not_hang_up_carrier_line(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app(tmp_path)) as c:
        a = c.post(
            "/calls",
            json={"request_id": str(uuid4()), "destination": "01000000001"},
            headers=HEADERS,
        ).json()
        path = f"/calls/{a['id']}"
        with c.websocket_connect(path + "/media", headers=HEADERS) as ws:
            assert ws.receive_json()["event"] == "start"
            ws.close()
            with pytest.raises(WebSocketDisconnect):
                for _ in range(20):
                    ws.receive_json()
        assert c.get(path, headers=HEADERS).json()["status"] == "active"
        ended = c.post(path + "/hangup", headers=HEADERS).json()
        assert ended["end_reason"] == "hangup_requested"
