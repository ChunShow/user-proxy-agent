import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_service.main import create_app


def test_unconfigured_status_and_oauth_require_local_session(tmp_path):
    app = create_app(database_path=tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        assert client.get("/api/integrations/google").status_code == 401
        client.post("/api/session", json={})
        r = client.get("/api/integrations/google")
        assert r.status_code == 200
        assert r.json()["status"] == "not_configured"
        assert "token" not in r.text and r.headers["cache-control"] == "no-store"
        assert (
            client.post(
                "/api/integrations/google/connect", json={}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.post("/api/integrations/google/connect", json={}).status_code == 409


def configured(tmp_path):
    from agent_service.integrations.google import GoogleManager
    from agent_service.integrations.settings import READ_SCOPES, GoogleSettings

    app = create_app(database_path=tmp_path / "db.sqlite3")
    requests = []

    async def provider(request):
        requests.append(request)
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "expires_in": 3600,
                    "scope": " ".join(READ_SCOPES),
                    "token_type": "Bearer",
                },
            )
        if request.url.path == "/oauth2/v2/userinfo":
            return httpx.Response(200, json={"email": "test@example.test", "verified_email": True})
        if request.url.path == "/revoke":
            return httpx.Response(200)
        return httpx.Response(500)

    app.state.integrations = GoogleManager(
        app.state.store,
        settings_loader=lambda: GoogleSettings(
            "client.test", "secret-test", "http://127.0.0.1:5180/api/integrations/google/callback"
        ),
        transport=httpx.MockTransport(provider),
    )
    app.state.calls.integrations = app.state.integrations
    return app, requests


def connect(client):
    r = client.post("/api/integrations/google/connect", json={})
    assert r.status_code == 200
    params = parse_qs(urlsplit(r.json()["url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    return params["state"][0]


def test_oauth_cookie_binding_one_use_encryption_and_disconnect(tmp_path):
    app, requests = configured(tmp_path)
    with TestClient(app) as client:
        client.post("/api/session", json={})
        state = connect(client)
        cookie = client.cookies.get("proxy_google_oauth")
        client.cookies.delete("proxy_google_oauth")
        url = f"/api/integrations/google/callback?state={state}&code=test-code"
        assert client.get(url, follow_redirects=False).status_code == 303
        assert not requests
        client.cookies.set("proxy_google_oauth", cookie)
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and "connected" in r.headers["location"]
        data = client.get("/api/integrations/google").json()
        assert data["status"] == "connected" and data["email"] == "test@example.test"
        assert data["calendar_read"] and data["gmail_read"]
        assert "private-access" not in json.dumps(data)
        assert b"private-refresh" not in (tmp_path / "db.sqlite3").read_bytes()
        assert (tmp_path / "integration.key").stat().st_mode & 0o777 == 0o600
        count = len(requests)
        client.cookies.set("proxy_google_oauth", cookie)
        client.get(url, follow_redirects=False)
        assert len(requests) == count
        r = client.post("/api/integrations/google/disconnect", json={})
        assert r.json()["status"] == "disconnected"
        assert client.get("/api/integrations/google").json()["email"] is None
        assert requests[-1].url.path == "/revoke"


def test_denied_flow_and_expired_state_do_not_connect(tmp_path):
    app, requests = configured(tmp_path)
    with TestClient(app) as client:
        client.post("/api/session", json={})
        state = connect(client)
        r = client.get(
            f"/api/integrations/google/callback?state={state}&error=access_denied&error_description=secret",
            follow_redirects=False,
        )
        assert "denied" in r.headers["location"] and "secret" not in r.text
        state = connect(client)
        with app.state.store.connection() as db:
            db.execute("UPDATE google_oauth_states SET expires_at=0")
        r = client.get(
            f"/api/integrations/google/callback?state={state}&code=code", follow_redirects=False
        )
        assert "invalid" in r.headers["location"] and not requests


@pytest.mark.asyncio
async def test_refresh_cannot_restore_disconnected_account(tmp_path):
    from agent_service.integrations.settings import READ_SCOPES
    from agent_service.storage import StoreError

    app, _ = configured(tmp_path)
    db = app.state.store
    db.initialize()
    g = app.state.integrations
    g.store.save(
        "owner",
        0,
        {"access_token": "old", "refresh_token": "refresh", "expires_at": 0},
        READ_SCOPES,
        "test@example.test",
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    g.transport = httpx.MockTransport(provider)
    task = asyncio.create_task(g.access("owner"))
    await entered.wait()
    g.store.disconnect("owner")
    release.set()
    with pytest.raises(StoreError):
        await task
    assert g.store.status("owner")["status"] == "disconnected"


def test_missing_key_does_not_replace_existing_ciphertext(tmp_path):
    from agent_service.integrations.settings import READ_SCOPES
    from agent_service.storage import StoreError

    app, _ = configured(tmp_path)
    app.state.store.initialize()
    store = app.state.integrations.store
    store.save("owner", 0, {"access_token": "private"}, READ_SCOPES, "one@example.test")
    store.key_path.unlink()
    with pytest.raises(StoreError, match="integration_key_missing"):
        store.decrypt(store.record("owner")["secret"])
    assert not store.key_path.exists()


@pytest.mark.asyncio
async def test_refresh_failure_requests_reconnect_without_leaking_provider_body(tmp_path):
    from agent_service.integrations.settings import READ_SCOPES
    from agent_service.storage import StoreError

    app, _ = configured(tmp_path)
    app.state.store.initialize()
    g = app.state.integrations
    g.store.save(
        "owner",
        0,
        {"access_token": "old", "refresh_token": "secret", "expires_at": 0},
        READ_SCOPES,
        "one@example.test",
    )
    g.transport = httpx.MockTransport(
        lambda r: httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "private"}
        )
    )
    with pytest.raises(StoreError, match="integration_reconnect_required") as error:
        await g.access("owner")
    assert "private" not in str(error.value)
    assert g.store.status("owner")["status"] == "reconnect_required"
    assert g.store.status("other")["status"] == "disconnected"


@pytest.mark.asyncio
async def test_parallel_access_refreshes_once_and_failed_revoke_still_removes_tokens(tmp_path):
    from agent_service.integrations.settings import READ_SCOPES

    app, requests = configured(tmp_path)
    app.state.store.initialize()
    g = app.state.integrations
    g.store.save(
        "owner",
        0,
        {"access_token": "old", "refresh_token": "secret", "expires_at": 0},
        READ_SCOPES,
        "one@example.test",
    )
    results = await asyncio.gather(g.access("owner"), g.access("owner"))
    assert len(requests) == 1 and results[0][0] == results[1][0] == "private-access"
    g.transport = httpx.MockTransport(lambda r: httpx.Response(503))
    result = await g.disconnect("owner")
    assert result == {"status": "disconnected", "revoked": False}
    assert g.store.record("owner")["secret"] is None


def test_access_log_does_not_retain_callback_code():
    import logging

    from agent_service.integrations.logging import OAuthAccessFilter

    record = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        "%s %s %s %s %s",
        ("local", "GET", "/api/integrations/google/callback?code=PRIVATE&state=SECRET", "1.1", 303),
        None,
    )
    assert OAuthAccessFilter().filter(record)
    assert "PRIVATE" not in record.getMessage() and "SECRET" not in record.getMessage()


@pytest.mark.asyncio
async def test_reconnect_waits_until_old_remote_revocation_finishes(tmp_path):
    from agent_service.integrations.settings import READ_SCOPES
    from agent_service.storage import StoreError

    app, _ = configured(tmp_path)
    app.state.store.initialize()
    g = app.state.integrations
    g.store.save(
        "owner",
        0,
        {"access_token": "old", "refresh_token": "refresh", "expires_at": 0},
        READ_SCOPES,
        "one@example.test",
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(r):
        entered.set()
        await release.wait()
        return httpx.Response(200)

    g.transport = httpx.MockTransport(provider)
    task = asyncio.create_task(g.disconnect("owner"))
    await entered.wait()
    try:
        with pytest.raises(StoreError, match="integration_busy"):
            await g.connect("owner")
    finally:
        release.set()
        await task
