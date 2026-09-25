import sqlite3

from fastapi.testclient import TestClient

from agent_service.main import create_app


def test_session_cookie_survives_restart_and_is_not_stored_plaintext(tmp_path):
    path = tmp_path / "private" / "test.sqlite3"
    with TestClient(create_app(database_path=path)) as client:
        response = client.post("/api/session", json={})
        assert response.status_code == 204
        token = client.cookies.get("proxy_session")
        assert token and len(token) >= 43
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Max-Age=2592000" in cookie
        assert client.post("/api/session", json={}).headers.get("set-cookie") is None
        owner = client.app.state.store.owner_for_token(token)
    with TestClient(create_app(database_path=path)) as client:
        client.cookies.set("proxy_session", token)
        assert client.post("/api/session", json={}).headers.get("set-cookie") is None
        assert client.app.state.store.owner_for_token(token) == owner
    assert token.encode() not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o077 == 0 for p in path.parent.iterdir())


def test_sessions_isolate_and_expire(tmp_path):
    with TestClient(create_app(database_path=tmp_path / "db.sqlite3")) as client:
        client.post("/api/session", json={})
        first = client.cookies.get("proxy_session")
        owner = client.app.state.store.owner_for_token(first)
        client.cookies.clear()
        client.post("/api/session", json={})
        second = client.cookies.get("proxy_session")
        assert client.app.state.store.owner_for_token(second) != owner
        with sqlite3.connect(tmp_path / "db.sqlite3") as db:
            db.execute("UPDATE sessions SET expires_at=0")
        assert client.app.state.store.owner_for_token(first) is None
        assert client.app.state.store.owner_for_token("invented") is None
        assert client.post("/api/session", json={}).headers.get("set-cookie")


def test_session_rejects_foreign_origin_and_forms(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_PORT", "5188")
    with TestClient(create_app(database_path=tmp_path / "db.sqlite3")) as client:
        assert (
            client.post(
                "/api/session", json={}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.post("/api/session", content="x=1").status_code == 422
        assert (
            client.post(
                "/api/session", json={}, headers={"Origin": "http://127.0.0.1:5188"}
            ).status_code
            == 204
        )
        assert client.post("/api/session", json={}, headers={"Origin": "null"}).status_code == 403
