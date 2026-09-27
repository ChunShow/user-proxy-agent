from uuid import uuid4

import pytest

from agent_service.chat import titles
from agent_service.chat.schemas import ChatRequest
from agent_service.storage import ConversationStore


@pytest.fixture
def saved(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite3")
    store.initialize()
    cid = str(uuid4())
    store.create_conversation("owner", cid)
    run = store.begin_run(
        "owner",
        ChatRequest(
            request_id=uuid4(), conversation_id=cid, content="내일 병원 진료 시간을 확인해 줘"
        ),
    )
    store.save_run("owner", run, "병원 진료 시간은 오전 9시부터입니다.", "completed")
    return store, cid


@pytest.mark.anyio
async def test_title_uses_first_pair_once(saved, monkeypatch):
    store, cid = saved
    seen = []

    async def generate(pair):
        seen.append(pair)
        return "병원 진료 시간 확인"

    monkeypatch.setattr(titles, "generate_title", generate)
    await titles.update_title(store, "owner", cid)
    await titles.update_title(store, "owner", cid)
    assert len(seen) == 1 and seen[0]["answer"].startswith("병원 진료")
    assert store.get_conversation("owner", cid)["conversation"]["title"] == "병원 진료 시간 확인"


@pytest.mark.anyio
async def test_manual_rename_wins_model_race(saved, monkeypatch):
    store, cid = saved

    async def generate(pair):
        store.rename_conversation("owner", cid, "내가 정한 제목")
        return "자동 제목"

    monkeypatch.setattr(titles, "generate_title", generate)
    await titles.update_title(store, "owner", cid)
    assert store.get_conversation("owner", cid)["conversation"]["title"] == "내가 정한 제목"


@pytest.mark.anyio
async def test_title_failure_preserves_fallback_and_messages(saved, monkeypatch):
    store, cid = saved
    before = store.get_conversation("owner", cid)

    async def fail(pair):
        raise RuntimeError("private provider error")

    monkeypatch.setattr(titles, "generate_title", fail)
    await titles.update_title(store, "owner", cid)
    assert store.get_conversation("owner", cid) == before
    assert store.claim_title("owner", cid) is None


def test_empty_or_failed_reply_never_claims_title(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite3")
    store.initialize()
    cid = str(uuid4())
    store.create_conversation("owner", cid)
    assert store.claim_title("owner", cid) is None
    run = store.begin_run(
        "owner", ChatRequest(request_id=uuid4(), conversation_id=cid, content="질문")
    )
    store.save_run("owner", run, "부분", "failed")
    assert store.claim_title("owner", cid) is None


def test_concurrent_claim_and_deletion_during_generation(saved):
    store, cid = saved
    assert store.claim_title("owner", cid)
    assert store.claim_title("owner", cid) is None
    store.delete_conversation("owner", cid)
    store.finish_title("owner", cid, "삭제 뒤 응답")
    store.restore_conversation("owner", cid)
    assert store.get_conversation("owner", cid)["conversation"]["title"] != "삭제 뒤 응답"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content", ["병원 진료 시간", [{"type": "text", "text": "병원 진료 시간"}]]
)
async def test_provider_accepts_string_and_responses_text_blocks(monkeypatch, content):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from agent_service.settings import Settings

    class Model:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        async def ainvoke(self, messages, config):
            assert "question" in messages[-1]["content"]
            return SimpleNamespace(content=content)

    monkeypatch.setattr(titles, "ChatOpenAI", Model)
    monkeypatch.setattr(
        titles, "load_settings", lambda: Settings("https://mock.test", SecretStr("fake"), "mock")
    )
    assert (
        await titles.generate_title({"question": "진료 시간?", "answer": "확인할게요"})
        == "병원 진료 시간"
    )
