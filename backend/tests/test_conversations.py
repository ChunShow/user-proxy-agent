from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(database_path=tmp_path / "db.sqlite3")) as client:
        client.post("/api/session", json={})
        yield client


def create(client):
    cid = str(uuid4())
    response = client.post("/api/conversations", json={"conversation_id": cid})
    assert response.status_code == 201
    return cid


def test_crud_is_owner_scoped_and_creation_is_idempotent(client):
    cid = create(client)
    again = client.post("/api/conversations", json={"conversation_id": cid})
    assert again.json()["id"] == cid
    assert len(client.get("/api/conversations").json()["items"]) == 1
    assert client.get(f"/api/conversations/{cid}").json()["messages"] == []
    token = client.cookies.get("proxy_session")
    client.cookies.clear()
    assert client.get("/api/conversations").status_code == 401
    client.post("/api/session", json={})
    assert client.get("/api/conversations").json()["items"] == []
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.post("/api/conversations", json={"conversation_id": cid}).status_code == 404
    client.cookies.clear()
    client.cookies.set("proxy_session", token)
    assert client.get(f"/api/conversations/{cid}").status_code == 200


def test_conversation_pagination_and_validation(client):
    ids = {create(client) for _ in range(53)}
    first = client.get("/api/conversations").json()
    assert len(first["items"]) == 50
    second = client.get("/api/conversations", params={"cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 3 and second["next_cursor"] is None
    assert {c["id"] for c in first["items"] + second["items"]} == ids
    assert client.get("/api/conversations?cursor=invalid").status_code == 422
    assert client.get("/api/conversations/not-uuid").status_code == 422
    assert (
        client.post(
            "/api/conversations", json={"conversation_id": str(uuid4()), "owner_id": "bad"}
        ).status_code
        == 422
    )


def test_rename_delete_restore_and_owner_scope(client):
    cid = create(client)
    path = f"/api/conversations/{cid}"
    assert client.post(path + "/rename", json={"title": "  새 제목  "}).json()["title"] == "새 제목"
    for title in ["", "  ", "a" * 81]:
        assert client.post(path + "/rename", json={"title": title}).status_code == 422
    assert client.post(path + "/delete", json={}).status_code == 200
    assert client.get(path).status_code == 404
    assert client.get("/api/conversations").json()["items"] == []
    assert client.get("/api/conversations?deleted=true").json()["items"][0]["id"] == cid
    token = client.cookies.get("proxy_session")
    client.cookies.clear()
    client.post("/api/session", json={})
    for action, body in [("rename", {"title": "침범"}), ("delete", {}), ("restore", {})]:
        assert client.post(path + "/" + action, json=body).status_code == 404
    client.cookies.clear()
    client.cookies.set("proxy_session", token)
    assert client.post(path + "/restore", json={}).status_code == 200
    assert client.get(path).json()["conversation"]["title"] == "새 제목"
    assert client.get("/api/conversations?deleted=true").json()["items"] == []


def test_mutations_reject_cross_origin_and_active_reply(client):
    from agent_service.chat.schemas import ChatRequest

    cid = create(client)
    path = f"/api/conversations/{cid}"
    for action in ["rename", "delete", "restore", "title"]:
        assert (
            client.post(
                path + "/" + action, json={}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
    store = client.app.state.store
    owner = store.owner_for_token(client.cookies.get("proxy_session"))
    store.begin_run(owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content="질문"))
    assert client.post(path + "/delete", json={}).status_code == 409
    assert client.get(path).status_code == 200


def test_deleted_conversation_cannot_be_reopened_for_chat(client):
    from agent_service.chat.schemas import ChatRequest
    from agent_service.storage import StoreError

    cid = create(client)
    store = client.app.state.store
    owner = store.owner_for_token(client.cookies.get("proxy_session"))
    store.delete_conversation(owner, cid)
    with pytest.raises(StoreError, match="not_found"):
        store.begin_run(owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content="질문"))


def test_active_call_blocks_deletion_and_terminal_call_is_preserved(client):
    from agent_service.calls.store import CallSpec, CallStore
    from agent_service.chat.schemas import ChatRequest

    cid = create(client)
    store = client.app.state.store
    owner = store.owner_for_token(client.cookies.get("proxy_session"))
    run = store.begin_run(
        owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content="질문")
    )
    store.save_run(owner, run, "답변", "completed")
    spec = CallSpec(
        destination="+821012345678",
        subject="UI 검증",
        purpose="가상 통화",
        opening_message="테스트",
        questions=["시간?"],
    )
    calls = CallStore(store)
    call = calls.register(owner, cid, run["user_message_id"], spec)
    assert client.post(f"/api/conversations/{cid}/delete", json={}).status_code == 409
    with store.connection() as db:
        db.execute("UPDATE phone_calls SET status='ended' WHERE id=?", (call["id"],))
    assert client.post(f"/api/conversations/{cid}/delete", json={}).status_code == 200
    assert calls.get(owner, call["id"])["status"] == "ended"
