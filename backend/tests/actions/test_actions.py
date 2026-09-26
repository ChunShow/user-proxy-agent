import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from agent_service.chat.schemas import ChatRequest
from agent_service.main import create_app
from agent_service.storage import StoreError


def setup(tmp_path, provider):
    from agent_service.actions.manager import ActionManager
    from agent_service.integrations.google import GoogleManager
    from agent_service.integrations.settings import WRITE_SCOPES

    app = create_app(database_path=tmp_path / "db.sqlite3")
    db = app.state.store
    db.initialize()
    owner = db.owner_for_token(db.issue_session())
    cid = str(uuid4())
    db.create_conversation(owner, cid)
    identity = db.begin_run(
        owner,
        ChatRequest(request_id=uuid4(), conversation_id=cid, content="일정 등록안을 만들어줘"),
    )
    db.save_run(owner, identity, "", "completed")
    google = GoogleManager(db, transport=httpx.MockTransport(provider))
    google.store.save(
        owner,
        0,
        {"access_token": "fake", "refresh_token": "fake", "expires_at": time.time() + 3600},
        WRITE_SCOPES,
        "person@example.test",
    )
    manager = ActionManager(db, google)
    context = SimpleNamespace(
        owner=owner, conversation_id=cid, source_user_message_id=identity["user_message_id"]
    )
    return db, manager, context


def event():
    return {
        "title": "시험 일정",
        "start": "2026-09-28T14:00:00+09:00",
        "end": "2026-09-28T15:00:00+09:00",
        "description": "",
        "location": "",
    }


@pytest.mark.asyncio
async def test_proposal_is_not_execution_and_concurrent_approval_sends_once(tmp_path):
    requests = []

    async def provider(r):
        requests.append(r)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"id": "event123"})

    db, m, ctx = setup(tmp_path, provider)
    a = await m.propose(ctx, "calendar_event", event())
    assert a["status"] == "pending" and not requests
    assert (await m.propose(ctx, "calendar_event", event()))["id"] == a["id"]
    with pytest.raises(StoreError):
        await m.propose(ctx, "calendar_event", event() | {"title": "different"})
    await asyncio.gather(*(m.approve(ctx.owner, a["id"], a["version"]) for _ in range(2)))
    await m.wait_idle()
    assert len(requests) == 1 and requests[0].method == "POST"
    result = m.store.get(ctx.owner, a["id"])
    assert result["status"] == "succeeded" and result["result"]["provider_id"] == "event123"
    assert (await m.approve(ctx.owner, a["id"], a["version"]))["status"] == "succeeded"
    with pytest.raises(StoreError):
        await m.approve("other", a["id"], a["version"])


@pytest.mark.asyncio
async def test_ambiguous_write_is_never_retried_and_restart_is_unknown(tmp_path):
    requests = []

    def provider(r):
        requests.append(r)
        raise httpx.ReadTimeout("private")

    db, m, ctx = setup(tmp_path, provider)
    a = await m.propose(
        ctx, "email", {"to": ["test@example.test"], "subject": "test", "body": "hello"}
    )
    await m.approve(ctx.owner, a["id"], a["version"])
    await m.wait_idle()
    assert m.store.get(ctx.owner, a["id"])["status"] == "unknown"
    await m.approve(ctx.owner, a["id"], a["version"])
    assert len(requests) == 1
    with db.connection() as conn:
        conn.execute("UPDATE proposed_actions SET status='executing'")
    m.store.recover()
    assert m.store.get(ctx.owner, a["id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_expiry_account_change_rejection_and_header_injection(tmp_path):
    db, m, ctx = setup(tmp_path, lambda r: pytest.fail("unexpected network"))
    with pytest.raises(StoreError):
        await m.propose(
            ctx,
            "email",
            {"to": ["a@example.test\r\nBcc: b@example.test"], "subject": "x", "body": "x"},
        )
    a = await m.propose(ctx, "calendar_event", event())
    with db.connection() as conn:
        conn.execute("UPDATE google_connections SET email='other@example.test'")
    with pytest.raises(StoreError, match="action_account_changed"):
        await m.approve(ctx.owner, a["id"], a["version"])
    with db.connection() as conn:
        conn.execute("UPDATE proposed_actions SET expires_at=0")
    assert m.store.get(ctx.owner, a["id"])["status"] == "expired"
    with pytest.raises(StoreError):
        await m.approve(ctx.owner, a["id"], a["version"])


@pytest.mark.asyncio
async def test_missing_write_scope_never_claims_execution(tmp_path):
    db, m, ctx = setup(tmp_path, lambda r: pytest.fail("unexpected network"))
    with db.connection() as conn:
        conn.execute("UPDATE google_connections SET scopes='[]'")
    a = await m.propose(ctx, "calendar_event", event())
    with pytest.raises(StoreError, match="integration_permission_required"):
        await m.approve(ctx.owner, a["id"], a["version"])
    assert m.store.get(ctx.owner, a["id"])["status"] == "pending"
    assert m.store.reject(ctx.owner, a["id"], a["version"])["status"] == "rejected"


@pytest.mark.asyncio
async def test_email_mime_exact_and_definite_rejection(tmp_path):
    import base64
    import json
    from email import policy
    from email.parser import BytesParser

    writes = []

    def provider(r):
        writes.append(r)
        return httpx.Response(403, json={"error": "private provider message"})

    db, m, ctx = setup(tmp_path, provider)
    a = await m.propose(
        ctx,
        "email",
        {
            "to": ["one@example.test", "two@example.test"],
            "subject": "시간 확인",
            "body": "오후 6시\n가능해요.",
        },
    )
    await m.approve(ctx.owner, a["id"], a["version"])
    await m.wait_idle()
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(json.loads(writes[0].content)["raw"])
    )
    assert str(message["To"]) == "one@example.test, two@example.test"
    assert str(message["Subject"]) == "시간 확인"
    assert message.get_content().strip() == "오후 6시\n가능해요."
    assert message["Bcc"] is None
    assert m.store.get(ctx.owner, a["id"])["status"] == "failed"


def test_routes_owner_origin_and_payload_are_enforced(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(database_path=tmp_path / "routes.sqlite3")
    with TestClient(app) as client:
        identity = str(uuid4())
        assert (
            client.post(
                f"/api/actions/{identity}/approve", json={"expected_version": 1}
            ).status_code
            == 401
        )
        client.post("/api/session", json={})
        assert (
            client.post(
                f"/api/actions/{identity}/approve",
                json={"expected_version": 1},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/actions/{identity}/approve", json={"expected_version": 1, "payload": {}}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/actions/{identity}/approve", json={"expected_version": True}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/actions/{identity}/approve", json={"expected_version": 1}
            ).status_code
            == 404
        )
        assert client.get(f"/api/conversations/{identity}/actions").status_code == 404


@pytest.mark.asyncio
async def test_disconnect_during_approval_does_not_abandon_claim(tmp_path):
    db, m, ctx = setup(tmp_path, lambda r: httpx.Response(200, json={"id": "created"}))
    a = await m.propose(ctx, "calendar_event", event())
    reached, release = asyncio.Event(), asyncio.Event()
    original = m.db

    async def pause(method, *args):
        result = await original(method, *args)
        if method == m.store.claim:
            reached.set()
            await release.wait()
        return result

    m.db = pause
    approval = asyncio.create_task(m.approve(ctx.owner, a["id"], a["version"]))
    await reached.wait()
    approval.cancel()
    with pytest.raises(asyncio.CancelledError):
        await approval
    release.set()
    await m.wait_idle()
    await m.wait_idle()
    assert m.store.get(ctx.owner, a["id"])["status"] == "succeeded"
