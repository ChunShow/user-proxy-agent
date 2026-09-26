from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agent_service.calls.store import CallSpec, CallStore
from agent_service.chat.schemas import ChatRequest
from agent_service.storage import ConversationStore, StoreError


def setup_store(tmp_path):
    conversations = ConversationStore(tmp_path / "db.sqlite3")
    conversations.initialize()
    owner = conversations.owner_for_token(conversations.issue_session())
    cid = str(uuid4())
    conversations.create_conversation(owner, cid)
    identity = add_user(conversations, owner, cid)
    return conversations, CallStore(conversations), owner, cid, identity["user_message_id"]


def add_user(db, owner, cid, content="010-0000-0001로 전화해서 테스트 가능한지 물어봐 줘"):
    identity = db.begin_run(
        owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content=content)
    )
    db.save_run(owner, identity, "", "completed")
    return identity


def spec(**overrides):
    return CallSpec(
        **(
            dict(
                destination="01000000001",
                subject="테스트",
                purpose="테스트 가능 여부 확인",
                opening_message="AI 테스트 도우미입니다.",
                questions=["테스트 가능한가요?"],
            )
            | overrides
        )
    )


def test_migration_owner_dedup_and_single_global_slot(tmp_path):
    db, calls, owner, cid, uid = setup_store(tmp_path)
    a = calls.register(owner, cid, uid, spec())
    assert calls.register(owner, cid, uid, spec()) == a
    with pytest.raises(StoreError) as e:
        calls.register(owner, cid, uid, spec(purpose="다른 목적"))
    assert e.value.code == "call_request_conflict"
    with pytest.raises(StoreError) as e:
        calls.get("other", a["id"])
    assert e.value.status == 404
    next_uid = add_user(db, owner, cid)["user_message_id"]
    with pytest.raises(StoreError) as e:
        calls.register(owner, cid, next_uid, spec())
    assert e.value.code == "call_busy"
    assert len(db.get_conversation(owner, cid)["messages"]) == 4
    db.initialize()
    with db.connection() as sql:
        assert sql.execute("SELECT version FROM schema_version").fetchall()[0][0] == 4
    assert calls.get(owner, a["id"])["status"] == "preparing"


def test_dial_claim_is_atomic_and_unknown_retains_slot(tmp_path):
    db, calls, owner, cid, uid = setup_store(tmp_path)
    a = calls.register(owner, cid, uid, spec())
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: calls.claim_dial(a["id"]), range(2)))
    assert sum(claims) == 1
    calls.update(a["id"], status="unknown", error_code="call_delivery_unknown")
    assert calls.active(owner)[0]["status"] == "unknown"
    with pytest.raises(StoreError):
        calls.register(owner, cid, add_user(db, owner, cid)["user_message_id"], spec())


def test_stop_before_claim_prevents_dial_and_registration_validates_anchor(tmp_path):
    db, calls, owner, cid, uid = setup_store(tmp_path)
    a = calls.register(owner, cid, uid, spec())
    calls.request_stop(owner, a["id"])
    assert not calls.claim_dial(a["id"])
    calls.update(a["id"], status="canceled", outcome="canceled")
    assert calls.active(owner) == []
    with pytest.raises(StoreError):
        calls.register(owner, cid, str(uuid4()), spec())
    assert calls.list(owner, cid)["items"][0]["id"] == a["id"]
    assert calls.get(owner, a["id"])["version"] > a["version"]


def test_registration_concurrency_allows_one_job(tmp_path):
    db, calls, owner, cid, uid = setup_store(tmp_path)
    uid2 = add_user(db, owner, cid)["user_message_id"]

    def register(anchor):
        try:
            return calls.register(owner, cid, anchor, spec())
        except StoreError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, [uid, uid2]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "call_busy" in results


def test_call_pages_do_not_leak_other_conversations(tmp_path):
    db, calls, owner, cid, uid = setup_store(tmp_path)
    for _ in range(51):
        a = calls.register(owner, cid, uid, spec())
        calls.update(a["id"], status="canceled", outcome="canceled")
        uid = add_user(db, owner, cid)["user_message_id"]
    page = calls.list(owner, cid)
    tail = calls.list(owner, cid, page["next_cursor"])
    assert len(page["items"]) == 50 and len(tail["items"]) == 1
    assert len({c["id"] for c in page["items"] + tail["items"]}) == 51
    with pytest.raises(StoreError):
        calls.list("other", cid)
