from uuid import uuid4

from fastapi.testclient import TestClient

from agent_service.calls.store import CallSpec
from agent_service.chat.schemas import ChatRequest
from agent_service.main import create_app


def seed(app, client):
    client.post("/api/session", json={})
    store = app.state.store
    owner = store.owner_for_token(client.cookies.get("proxy_session"))
    cid = str(uuid4())
    store.create_conversation(owner, cid)
    identity = store.begin_run(
        owner,
        ChatRequest(request_id=uuid4(), conversation_id=cid, content="01000000001로 전화해줘"),
    )
    store.save_run(owner, identity, "", "completed")
    call = app.state.calls.store.register(
        owner,
        cid,
        identity["user_message_id"],
        CallSpec(
            destination="01000000001",
            subject="테스트",
            purpose="확인",
            opening_message="AI입니다.",
            questions=["가능한가요?"],
        ),
    )
    return cid, call


def test_call_endpoints_owner_origin_cache_and_no_dial(tmp_path):
    app = create_app(database_path=tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        assert client.get("/api/calls/active").status_code == 401
        cid, call = seed(app, client)
        url = f"/api/calls/{call['id']}"
        assert client.get(url).json()["id"] == call["id"]
        assert client.get(url).headers["cache-control"] == "no-store"
        assert "provider_call_id" not in client.get(url).json()
        assert client.get("/api/calls/active").json()["items"][0]["id"] == call["id"]
        assert client.get(f"/api/conversations/{cid}/calls").json()["items"][0]["id"] == call["id"]
        assert (
            client.post(url + "/stop", json={}, headers={"Origin": "https://evil.test"}).status_code
            == 403
        )
        assert client.post(url + "/stop", json={"destination": "01000000002"}).status_code == 422
        assert client.post(url + "/stop", json={}).status_code == 200
        assert client.post(url + "/stop", json={}).status_code == 200
        assert client.post(url + "/refresh", json={}).json()["status"] == "canceled"
        client.cookies.clear()
        client.post("/api/session", json={})
        assert client.get(url).status_code == 404
        assert client.post(url + "/stop", json={}).status_code == 404
        assert client.get(f"/api/conversations/{cid}/calls").status_code == 404
        assert client.get("/api/calls/active").json()["items"] == []


def test_confirmation_routes_validate_identity_revision_and_no_redial(tmp_path):
    from agent_service.calls.live_store import LiveStore

    app = create_app(database_path=tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        _, call = seed(app, client)
        calls = app.state.calls.store
        calls.update(call["id"], status="connected")
        live = LiveStore(calls)
        live.begin(call["id"], "d1")
        q = live.ask(call["id"], "d1", 1, "가능한가요?", ["가능", "불가"])
        url = f"/api/calls/{call['id']}/confirmations/{q['id']}/answer"
        body = {"answer": "가능", "expected_revision": 1, "request_id": str(uuid4())}
        assert (
            client.post(url, json=body, headers={"Origin": "https://evil.test"}).status_code == 403
        )
        assert client.post(url, json=body | {"expected_revision": 2}).status_code == 409
        assert client.post(url, json=body).json()["status"] == "answered"
        assert client.post(url, json=body).json()["status"] == "answered"
        restored = client.get(f"/api/calls/{call['id']}").json()
        assert restored["confirmations"][0]["answer"] == "가능"
        assert calls.record(call["id"])["dial_attempted_at"] is None
        client.cookies.clear()
        client.post("/api/session", json={})
        assert client.get(f"/api/calls/{call['id']}/activity").status_code == 404
        assert client.post(url, json=body).status_code == 404


def test_activity_cursor_validation_and_empty_terminal_page(tmp_path):
    app = create_app(database_path=tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        _, call = seed(app, client)
        app.state.calls.store.update(call["id"], status="ended")
        url = f"/api/calls/{call['id']}/activity"
        assert client.get(url + "?after=-1").status_code == 422
        response = client.get(url + "?after=12")
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "events": [],
            "questions": [],
            "next_after": 12,
            "has_more": False,
            "terminal": True,
        }
