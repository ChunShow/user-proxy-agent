import asyncio
from contextlib import asynccontextmanager

import pytest
from test_call_settings import values
from test_call_store import add_user, setup_store, spec
from test_native_audio import until

from agent_service.calls.manager import CallManager
from agent_service.calls.settings import CallSettings
from agent_service.calls.types import CallSnapshot, DialRejected, DialUncertain, ProviderFailure
from agent_service.storage import StoreError

pytestmark = pytest.mark.asyncio


class FakeGateway:
    def __init__(self):
        self.dials = 0
        self.hangups = 0
        self.lookups = 0
        self.dial_gate = asyncio.Event()
        self.dial_gate.set()
        self.prep_gate = asyncio.Event()
        self.prep_gate.set()
        self.audio_gate = asyncio.Event()
        self.dial_error = None
        self.hangup_error = False
        self.lookup_error = False
        self.status = "active"
        self.report = {}
        self.closed = False

    def snapshot(self):
        return CallSnapshot("CAtest", self.status, "07011112222", "01000000001")

    @asynccontextmanager
    async def open(self, settings):
        try:
            yield self
        finally:
            self.closed = True

    async def preflight(self):
        await self.prep_gate.wait()

    @asynccontextmanager
    async def audio(self, spec):
        yield None

    @asynccontextmanager
    async def connection(self):
        yield self

    async def dial(self, call_id, destination):
        self.dials += 1
        await self.dial_gate.wait()
        if self.dial_error:
            raise self.dial_error
        return self.snapshot()

    async def lookup(self, provider_id):
        self.lookups += 1
        if self.lookup_error:
            raise ProviderFailure("clawops_transport_failed")
        return self.snapshot()

    async def hangup(self, provider_id):
        self.hangups += 1
        if self.hangup_error:
            raise ProviderFailure("clawops_hangup_failed")
        self.status = "completed"
        return self.snapshot()

    @asynccontextmanager
    async def media(self, provider_id):
        yield None

    def bridge(self, model, spec):
        return self

    async def run(self, media):
        await self.audio_gate.wait()
        return self.report

    async def clear(self):
        pass


def manager(tmp_path, **options):
    db, store, owner, cid, uid = setup_store(tmp_path)
    gateway = FakeGateway()
    settings = CallSettings.load(tmp_path / "missing", environ=values())
    m = CallManager(
        store,
        settings_loader=lambda: settings,
        gateway_factory=gateway.open,
        poll_seconds=0.005,
        confirmation_attempts=2,
        **options,
    )
    return m, gateway, db, store, owner, cid, uid


async def test_dedup_independent_work_and_direct_stop(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    a = await m.start(o, c, u, spec())
    duplicate = await m.start(o, c, u, spec())
    assert duplicate["id"] == a["id"]
    await until(lambda: g.dials == 1)
    await until(lambda: s.get(o, a["id"])["status"] == "connected")
    # The HTTP/chat caller has returned, but work is still alive.
    assert not g.closed
    await m.stop(o, a["id"])
    await m.stop(o, a["id"])
    await m.wait_idle()
    assert g.hangups == 1 and g.dials == 1
    assert s.get(o, a["id"])["status"] == "ended"
    assert s.get(o, a["id"])["outcome"] == "canceled"
    await m.shutdown()


async def test_cancel_registration_waiter_does_not_orphan_job(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    task = asyncio.create_task(m.start(o, c, u, spec()))
    await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await until(lambda: g.dials == 1)
    await m.shutdown()
    assert g.hangups == 1 and s.active(o) == []


async def test_stop_during_dial_waits_for_id_then_hangs_up(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.dial_gate.clear()
    a = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    await m.stop(o, a["id"])
    assert g.hangups == 0
    g.dial_gate.set()
    await m.wait_idle()
    assert g.hangups == 1 and s.get(o, a["id"])["status"] == "ended"


async def test_stop_before_dial_skips_provider_creation(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.prep_gate.clear()
    a = await m.start(o, c, u, spec())
    await m.stop(o, a["id"])
    g.prep_gate.set()
    await m.wait_idle()
    assert g.dials == 0 and s.get(o, a["id"])["status"] == "canceled"


@pytest.mark.parametrize(
    "error,status", [(DialUncertain("unknown"), "unknown"), (DialRejected("rejected"), "failed")]
)
async def test_no_retry_on_uncertainty_or_rejection(tmp_path, error, status):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.dial_error = error
    a = await m.start(o, c, u, spec())
    await m.wait_idle()
    assert s.get(o, a["id"])["status"] == status
    assert (await m.start(o, c, u, spec()))["id"] == a["id"]
    await m.wait_idle()
    assert g.dials == 1
    if status == "unknown":
        await m.refresh(o, a["id"])
        assert g.hangups == 0 and s.get(o, a["id"])["status"] == "unknown"
    await m.shutdown()


async def test_hangup_failure_retains_slot_and_can_be_rechecked(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.hangup_error = True
    a = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    await m.stop(o, a["id"])
    await m.wait_idle()
    result = s.get(o, a["id"])
    assert result["status"] == "ending" and result["error_code"] == "call_end_unconfirmed"
    g.hangup_error = False
    await m.refresh(o, a["id"])
    assert s.get(o, a["id"])["status"] == "ended" and g.dials == 1


async def test_remote_end_and_time_limit_are_incomplete_not_success(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path, max_seconds_override=0.03)
    a = await m.start(o, c, u, spec())
    await m.wait_idle()
    assert s.get(o, a["id"])["status"] == "ended"
    assert s.get(o, a["id"])["outcome"] == "incomplete"
    assert g.hangups == 1
    b = await m.start(o, c, add_user(db, o, c)["user_message_id"], spec())
    # Carrier says it has already ended: no media and no second hangup needed.
    await m.wait_idle()
    assert s.get(o, b["id"])["status"] == "ended" and g.hangups == 1


@pytest.mark.parametrize(
    "played,expected", [(True, "model_reported_success"), (False, "incomplete")]
)
async def test_success_requires_playback_and_provider_confirmation(tmp_path, played, expected):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.report = {
        "end_call": {
            "reason": "goal_achieved",
            "status": "played" if played else "not_played",
            "summary": "테스트 답변 재확인",
        }
    }
    a = await m.start(o, c, u, spec())
    g.audio_gate.set()
    await m.wait_idle()
    assert s.get(o, a["id"])["outcome"] == expected
    assert g.hangups == 1


async def test_restart_never_dials_and_unknown_is_not_cleared(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    a = s.register(o, c, u, spec())
    s.claim_dial(a["id"])
    s.update(a["id"], provider_call_id="CAtest")
    await m.recover()
    await m.wait_idle()
    assert g.dials == 0 and g.hangups == 1
    b = s.register(o, c, add_user(db, o, c)["user_message_id"], spec())
    s.claim_dial(b["id"])
    await m.recover()
    await m.wait_idle()
    assert s.get(o, b["id"])["status"] == "unknown" and g.dials == 0
    await m.shutdown()


async def test_guessed_number_and_no_explicit_request_cannot_dial(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    with pytest.raises(StoreError) as e:
        await m.start(o, c, u, spec(destination="01000000002"))
    assert e.value.code == "call_number_not_allowed"
    uid = add_user(db, o, c, "통화 요금은 얼마야?")["user_message_id"]
    with pytest.raises(StoreError) as e:
        await m.start(o, c, uid, spec())
    assert e.value.code == "call_request_required"
    assert g.dials == 0


async def test_lookup_failure_does_not_prevent_attempt_to_hangup(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.lookup_error = True
    a = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    await m.stop(o, a["id"])
    await m.wait_idle()
    assert g.hangups >= 1
    assert s.get(o, a["id"])["status"] == "ended"


async def test_goal_evidence_survives_unconfirmed_hangup_and_refresh(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.hangup_error = True
    g.report = {"end_call": {"reason": "goal_achieved", "status": "played", "summary": "확인됨"}}
    a = await m.start(o, c, u, spec())
    g.audio_gate.set()
    await m.wait_idle()
    assert s.get(o, a["id"])["status"] == "ending"
    assert s.get(o, a["id"])["outcome"] == "pending"
    g.hangup_error = False
    await m.refresh(o, a["id"])
    assert s.get(o, a["id"])["outcome"] == "model_reported_success"


async def test_database_failure_after_dial_still_attempts_carrier_cleanup(tmp_path, monkeypatch):
    import sqlite3

    m, g, db, s, o, c, u = manager(tmp_path)
    original = s.update

    def broken(call_id, **changes):
        if g.dials:
            raise sqlite3.OperationalError("disk unavailable")
        return original(call_id, **changes)

    monkeypatch.setattr(s, "update", broken)
    await m.start(o, c, u, spec())
    await m.wait_idle()
    assert g.dials == 1 and g.hangups >= 1


async def test_disconnect_during_stop_persistence_still_stops_worker(tmp_path, monkeypatch):
    import threading

    m, g, db, s, o, c, u = manager(tmp_path)
    a = await m.start(o, c, u, spec())
    await until(lambda: s.get(o, a["id"])["status"] == "connected")
    entered, release = threading.Event(), threading.Event()
    original = s.request_stop

    def delayed(*args):
        entered.set()
        release.wait(2)
        return original(*args)

    monkeypatch.setattr(s, "request_stop", delayed)
    request = asyncio.create_task(m.stop(o, a["id"]))
    await until(entered.is_set)
    request.cancel()
    release.set()
    await asyncio.gather(request, return_exceptions=True)
    try:
        await until(lambda: g.hangups == 1)
    finally:
        await m.shutdown()
    assert s.get(o, a["id"])["status"] == "ended"


@pytest.mark.parametrize(
    "condition",
    [
        "통화 가능한지는 추측하지 말고 웹으로 확인해줘.",
        "전화 상대에게 예약은 하지 말고 시간만 물어봐.",
    ],
)
async def test_call_request_accepts_constraints_on_the_conversation(tmp_path, condition):
    m, g, db, s, o, c, u = manager(tmp_path)
    uid = add_user(db, o, c, "01000000001로 전화 걸어줘. " + condition)["user_message_id"]
    try:
        call = await m.start(o, c, uid, spec())
        await until(lambda: s.get(o, call["id"])["status"] == "connected")
        assert g.dials == 1
    finally:
        await m.shutdown()


@pytest.mark.parametrize(
    "user_request",
    [
        "01000000001로 전화하지 말고 문자 보내줘.",
        "01000000001로 전화는 절대 걸지 말아줘.",
        "01000000001로 전화 걸어줘. 아니 전화 걸지 마.",
        "01000000001로 통화 연결하지 말아 주세요.",
        "01000000001로 전화 걸어주지 마세요.",
        "01000000001로 전화 안 걸어줘도 돼.",
        "01000000001 통화 요금은 얼마야?",
    ],
)
async def test_call_request_still_rejects_direct_prohibitions(tmp_path, user_request):
    m, g, db, s, o, c, u = manager(tmp_path)
    uid = add_user(db, o, c, user_request)["user_message_id"]
    with pytest.raises(StoreError, match="call_request_required"):
        await m.start(o, c, uid, spec())
    assert g.dials == 0


@pytest.mark.parametrize("status", ["no_answer", "busy", "failed", "canceled"])
@pytest.mark.parametrize("during_media_wait", [False, True])
async def test_carrier_end_reason_survives_media_wait_failure(tmp_path, status, during_media_wait):
    m, g, db, s, o, c, u = manager(tmp_path)

    @asynccontextmanager
    async def disconnected_media(provider_id):
        g.status = status
        raise ProviderFailure("clawops_call_not_connected")
        yield  # pragma: no cover

    if during_media_wait:
        g.media = disconnected_media
    else:
        g.status = status
    call = await m.start(o, c, u, spec())
    await m.wait_idle()
    result = s.get(o, call["id"])
    assert result["status"] == "ended"
    assert result["error_code"] == f"call_{status}"
    assert result["outcome"] == "incomplete"
    assert g.dials == 1 and g.hangups == 0
    assert s.active(o) == []


async def test_audio_failure_on_connected_line_is_not_mislabeled_as_no_answer(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)

    @asynccontextmanager
    async def broken_media(provider_id):
        raise ProviderFailure("clawops_stream_failed")
        yield  # pragma: no cover

    g.media = broken_media
    call = await m.start(o, c, u, spec())
    await m.wait_idle()
    result = s.get(o, call["id"])
    assert result["status"] == "ended"
    assert result["error_code"] == "call_audio_failed"
    assert g.hangups == 1 and g.dials == 1
