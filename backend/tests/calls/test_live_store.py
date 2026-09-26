import pytest
from test_call_store import setup_store, spec

from agent_service.storage import StoreError


def setup_live(tmp_path):
    from agent_service.calls.live_store import LiveStore

    db, calls, owner, cid, uid = setup_store(tmp_path)
    call = calls.register(owner, cid, uid, spec())
    calls.update(call["id"], status="connected")
    live = LiveStore(calls)
    return db, calls, live, owner, call["id"]


def test_answer_owner_idempotency_and_revision(tmp_path):
    _, calls, live, owner, call = setup_live(tmp_path)
    assert live.begin(call, "d1") == 1
    assert live.begin(call, "d1") is None
    q = live.ask(call, "d1", 1, "화요일 오후 3시 가능한가요?", ["가능해요", "어려워요"], 60)
    with pytest.raises(StoreError):
        live.answer("other", call, q["id"], "가능해요", 1, "r1")
    result = live.answer(owner, call, q["id"], "가능해요", 1, "r1")
    assert result["status"] == "answered"
    assert live.answer(owner, call, q["id"], "가능해요", 1, "r1") == result
    with pytest.raises(StoreError):
        live.answer(owner, call, q["id"], "아니요", 1, "r1")
    assert live.begin(call, "d2") == 2
    assert not live.current(call, "d1", 1)
    assert live.activity(owner, call)["questions"][0]["status"] == "canceled"
    calls.request_stop(owner, call)
    assert not live.current(call, "d2", 2)


def test_timeout_and_restart_never_approve(tmp_path):
    db, _, live, owner, call = setup_live(tmp_path)
    live.begin(call, "d1")
    q = live.ask(call, "d1", 1, "가능한가요?", [], -1)
    with pytest.raises(StoreError):
        live.answer(owner, call, q["id"], "네", 1, "r1")
    assert live.activity(owner, call)["questions"][0]["status"] == "expired"
    live.begin(call, "d2")
    q2 = live.ask(call, "d2", 2, "다른 시간이 있나요?", [], 60)
    db.initialize()
    live.cancel(call)
    assert all(q["status"] != "pending" for q in live.activity(owner, call)["questions"])
    assert live.question(call, q2["id"])["answer"] is None


def test_activity_pages_recover_final_transcript_without_duplicates(tmp_path):
    _, calls, live, owner, call = setup_live(tmp_path)
    for i in range(205):
        live.event(call, "transcript", {"role": "caller", "text": str(i)})
    first = live.activity(owner, call)
    assert len(first["events"]) == 100 and first["has_more"]
    assert not first["terminal"]
    calls.update(call, status="ended")
    second = live.activity(owner, call, first["next_after"])
    last = live.activity(owner, call, second["next_after"])
    assert second["terminal"] and second["has_more"]
    assert last["terminal"] and not last["has_more"]
    events = first["events"] + second["events"] + last["events"]
    assert [e["content"]["text"] for e in events] == [str(i) for i in range(205)]
    empty = live.activity(owner, call, last["next_after"])
    assert empty["events"] == [] and empty["next_after"] == last["next_after"]
    with pytest.raises(StoreError):
        live.activity("other", call)
