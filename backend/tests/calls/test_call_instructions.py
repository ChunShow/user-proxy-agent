from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_call_store import add_user
from test_live_store import setup_live

from agent_service.storage import StoreError


def setup_instructions(tmp_path):
    from agent_service.calls.instructions import InstructionStore

    db, calls, live, owner, call = setup_live(tmp_path)
    cid = calls.record(call)["conversation_id"]
    uid = add_user(db, owner, cid, "오후 6시로 바꿔줘")["user_message_id"]
    ctx = SimpleNamespace(owner=owner, conversation_id=cid, source_user_message_id=uid)
    return db, calls, live, InstructionStore(calls), ctx, call


def test_submit_invalidates_old_question_and_deduplicates(tmp_path):
    db, calls, live, store, ctx, call = setup_instructions(tmp_path)
    rev = live.begin(call, "d1")
    q = live.ask(call, "d1", rev, "3시 가능한가요?", [])
    before = calls.get(ctx.owner, call)["version"]
    item = store.submit(ctx, call, "오후 6시로 확인")
    assert item["status"] == "pending"
    assert item["condition_revision"] == 1
    assert store.submit(ctx, call, "오후 6시로 확인") == item
    assert not live.current(call, "d1", rev)
    assert live.begin(call, "while_pending") is None
    with pytest.raises(StoreError):
        live.answer(ctx.owner, call, q["id"], "가능", rev, "late")
    assert calls.get(ctx.owner, call)["confirmations"][0]["status"] == "canceled"
    assert calls.get(ctx.owner, call)["instructions"] == [item]
    assert calls.get(ctx.owner, call)["version"] > before
    with pytest.raises(StoreError, match="instruction_conflict"):
        store.submit(ctx, call, "다른 내용")
    assert store.transition(call, item["id"], "pending", "sending")["status"] == "sending"
    assert store.transition(call, item["id"], "sending", "delivered")["status"] == "delivered"
    assert live.begin(call, "d2") == 2
    db.initialize()
    assert calls.get(ctx.owner, call)["instructions"][0]["status"] == "delivered"


def test_owner_anchor_conversation_and_concurrent_pending_restrictions(tmp_path):
    db, calls, _, store, ctx, call = setup_instructions(tmp_path)
    for patch in [
        {"owner": "other"},
        {"conversation_id": str(uuid4())},
        {"source_user_message_id": str(uuid4())},
    ]:
        with pytest.raises(StoreError):
            store.submit(SimpleNamespace(**(vars(ctx) | patch)), call, "지시")
    uid2 = add_user(db, ctx.owner, ctx.conversation_id, "가격도 확인")["user_message_id"]
    other = SimpleNamespace(**(vars(ctx) | {"source_user_message_id": uid2}))

    def submit(context):
        try:
            return store.submit(context, call, "가격 확인")["status"]
        except StoreError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [ctx, other]))
    assert sorted(results) == ["instruction_busy", "pending"]
    with pytest.raises(StoreError):
        calls.get("other", call)


@pytest.mark.parametrize("started,expected", [(False, "not_applied"), (True, "delivery_unknown")])
def test_stop_or_recovery_never_replays_or_claims_delivery(tmp_path, started, expected):
    _, calls, live, store, ctx, call = setup_instructions(tmp_path)
    item = store.submit(ctx, call, "새 조건")
    if started:
        store.transition(call, item["id"], "pending", "sending")
    calls.request_stop(ctx.owner, call)
    store.abort(call, "call_ended")
    assert calls.get(ctx.owner, call)["instructions"][0]["status"] == expected
    assert store.transition(call, item["id"], "sending", "delivered")["status"] == expected
    assert live.begin(call, "new") is None
    assert store.submit(ctx, call, "새 조건")["status"] == expected


def test_delivery_after_stop_is_unknown_and_new_requests_are_rejected(tmp_path):
    _, calls, _, store, ctx, call = setup_instructions(tmp_path)
    item = store.submit(ctx, call, "새 조건")
    store.transition(call, item["id"], "pending", "sending")
    calls.request_stop(ctx.owner, call)
    assert (
        store.transition(call, item["id"], "sending", "delivered")["status"] == "delivery_unknown"
    )


def test_instruction_bounds_and_limit(tmp_path):
    db, calls, _, store, ctx, call = setup_instructions(tmp_path)
    for text in ["", " ", "x" * 2001]:
        with pytest.raises(StoreError):
            store.submit(ctx, call, text)
    for _ in range(30):
        ctx.source_user_message_id = add_user(db, ctx.owner, ctx.conversation_id, "확인")[
            "user_message_id"
        ]
        item = store.submit(ctx, call, "확인")
        store.transition(call, item["id"], "pending", "sending")
        store.transition(call, item["id"], "sending", "delivered")
    ctx.source_user_message_id = add_user(db, ctx.owner, ctx.conversation_id, "추가")[
        "user_message_id"
    ]
    with pytest.raises(StoreError, match="instruction_limit"):
        store.submit(ctx, call, "추가")
    assert len(calls.get(ctx.owner, call)["instructions"]) == 30


def test_upgrade_old_database_preserves_calls_questions_and_transcripts(tmp_path):
    db, calls, live, _, ctx, call = setup_instructions(tmp_path)
    live.begin(call, "old")
    q = live.ask(call, "old", 1, "合成質問", [])
    live.event(call, "transcript", {"role": "caller", "text": "合成音声"})
    with db.connection() as sql:
        sql.execute("DROP TABLE call_instructions")
        sql.execute("ALTER TABLE phone_calls DROP COLUMN condition_revision")
        sql.execute("ALTER TABLE call_delegations DROP COLUMN condition_revision")
        sql.execute("UPDATE schema_version SET version=3")
    db.initialize()
    db.initialize()
    assert calls.record(call)["condition_revision"] == 0
    assert live.question(call, q["id"])["status"] == "pending"
    assert live.activity(ctx.owner, call)["events"][-1]["content"]["text"] == "合成音声"
    assert calls.get(ctx.owner, call)["instructions"] == []


@pytest.mark.parametrize("status", ["preparing", "dialing", "ending", "ended", "failed"])
def test_non_connected_call_rejects_new_instruction(tmp_path, status):
    _, calls, _, store, ctx, call = setup_instructions(tmp_path)
    calls.update(call, status=status)
    with pytest.raises(StoreError, match="call_instruction_inactive"):
        store.submit(ctx, call, "새 지시")


def test_unacknowledged_conditions_block_delegation_until_new_explicit_instruction(tmp_path):
    db, _, live, store, ctx, call = setup_instructions(tmp_path)
    item = store.submit(ctx, call, "변경")
    store.transition(call, item["id"], "pending", "sending")
    store.abort(call, "delivery_unconfirmed")
    assert live.begin(call, "must_not_replay") is None
    ctx.source_user_message_id = add_user(db, ctx.owner, ctx.conversation_id, "다시 변경")[
        "user_message_id"
    ]
    new = store.submit(ctx, call, "다시 변경")
    store.transition(call, new["id"], "pending", "sending")
    store.transition(call, new["id"], "sending", "delivered")
    assert live.begin(call, "new") == 1
    assert [i["text"] for i in store.delivered(call)] == ["다시 변경"]
