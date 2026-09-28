import asyncio
import time

import pytest
from test_call_store import add_user, spec
from test_manager import manager
from test_native_audio import until

from agent_service.storage import StoreError

pytestmark = pytest.mark.asyncio


async def test_request_waits_without_provider_work_and_new_number_can_be_approved(tmp_path):
    m, g, db, s, o, c, _ = manager(tmp_path)
    uid = add_user(db, o, c, "01000000002로 전화해서 테스트 가능한지 물어봐 줘")["user_message_id"]
    try:
        call = await m.start(o, c, uid, spec(destination="01000000002"))
        assert call["status"] == "awaiting_approval"
        await m.wait_idle()
        assert not m.tasks and g.dials == 0 and not g.closed
        assert not s.claim_dial(call["id"])
        results = await asyncio.gather(*[
            m.approve(o, call["id"], call["version"]) for _ in range(2)
        ])
        assert all(r["id"] == call["id"] for r in results)
        await until(lambda: g.dials == 1)
        assert s.record(call["id"])["approved_at"] is not None
        await m.approve(o, call["id"], call["version"])
        assert g.dials == 1
    finally:
        await m.shutdown()


async def test_approval_owner_version_cancel_and_expiry_never_dial(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    call = await m.start(o, c, u, spec())
    assert call["status"] == "awaiting_approval"
    with pytest.raises(StoreError, match="not_found"):
        await m.approve("other", call["id"], call["version"])
    with pytest.raises(StoreError, match="call_approval_changed"):
        await m.approve(o, call["id"], call["version"] + 1)
    await m.stop(o, call["id"])
    with pytest.raises(StoreError, match="call_approval_inactive"):
        await m.approve(o, call["id"], call["version"])
    uid = add_user(db, o, c)["user_message_id"]
    expired = await m.start(o, c, uid, spec())
    with db.connection() as conn:
        conn.execute("UPDATE phone_calls SET approval_expires_at=? WHERE id=?",
                     (time.time() - 1, expired["id"]))
    with pytest.raises(StoreError, match="call_approval_inactive"):
        await m.approve(o, expired["id"], expired["version"])
    assert (await m.get(o, expired["id"]))["error_code"] == "call_approval_expired"
    assert g.dials == 0
    await m.shutdown()


async def test_pending_refresh_and_restart_never_start_a_call(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    call = await m.start(o, c, u, spec())
    assert call["status"] == "awaiting_approval"
    assert (await m.refresh(o, call["id"]))["status"] == "awaiting_approval"
    await m.recover()
    await m.wait_idle()
    assert (await m.get(o, call["id"]))["status"] == "canceled"
    assert g.dials == 0
    await m.shutdown()


async def test_forged_preparing_state_without_approval_cannot_claim_dial(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    call = await m.start(o, c, u, spec())
    s.update(call["id"], status="preparing")
    assert not s.claim_dial(call["id"])
    await m.recover()
    await m.wait_idle()
    assert g.dials == 0
    await m.shutdown()


async def test_expiration_releases_slot_without_call_report_or_provider_access(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    call = await m.start(o, c, u, spec())
    with db.connection() as conn:
        conn.execute("UPDATE phone_calls SET approval_expires_at=0 WHERE id=?", (call["id"],))
    assert s.active(o) == []
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM call_reports").fetchone()[0] == 0
    uid = add_user(db, o, c)["user_message_id"]
    pending = await m.start(o, c, uid, spec())
    assert pending["id"] != call["id"] and g.dials == 0
    await m.stop(o, pending["id"])
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM call_reports").fetchone()[0] == 0
    await m.shutdown()
