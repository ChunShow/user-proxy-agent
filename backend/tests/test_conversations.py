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
