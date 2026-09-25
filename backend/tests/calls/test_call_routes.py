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
