import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_conversations import create

from agent_service.chat import routes
from agent_service.main import create_app
from agent_service.settings import Settings


def start(client, cid, **extra):
    return client.post(
        "/api/chat", json={"request_id": str(uuid4()), "conversation_id": cid, **extra}
    )


def test_restore_context_retry_and_idempotency(tmp_path, monkeypatch):
    calls = []

    async def reply(messages, settings, call_context=None):
        calls.append(messages)
        yield "합성 응답"

    monkeypatch.setattr(routes, "stream_reply", reply)
    monkeypatch.setattr(
        routes, "load_settings", lambda: Settings("https://model.test", SecretStr("fake"), "test")
    )
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(database_path=path)) as client:
        client.post("/api/session", json={})
        token = client.cookies.get("proxy_session")
        cid = create(client)
        payload = {"request_id": str(uuid4()), "conversation_id": cid, "content": "기억 482"}
        assert "event: done" in client.post("/api/chat", json=payload).text
        duplicate = client.post("/api/chat", json=payload)
        assert (
            duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "request_exists"
        )
        assert client.post("/api/chat", json=payload | {"content": "변경"}).status_code == 409
        saved = client.get(f"/api/conversations/{cid}").json()
        assert [m["text"] for m in saved["messages"]] == ["기억 482", "합성 응답"]
        assert saved["conversation"]["title"] == "기억 482"
        assert len(calls) == 1
    with TestClient(create_app(database_path=path)) as client:
        client.cookies.set("proxy_session", token)
        assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 2
        assert len(calls) == 1
        assert start(client, cid, content="뭐였지?").status_code == 200
        assert calls[-1] == [
            {"role": "user", "content": "기억 482"},
            {"role": "assistant", "content": "합성 응답"},
            {"role": "user", "content": "뭐였지?"},
        ]
        other = create(client)
        start(client, other, content="별개")
        assert calls[-1] == [{"role": "user", "content": "별개"}]
        client.cookies.clear()
        client.post("/api/session", json={})
        assert start(client, cid, content="접근").status_code == 404
        assert len(calls) == 3


def test_partial_failure_retry_and_restart_interruption(tmp_path, monkeypatch):
    async def broken(*args):
        yield "중간"
        raise RuntimeError("private secret")

    monkeypatch.setattr(routes, "stream_reply", broken)
    monkeypatch.setattr(
        routes, "load_settings", lambda: Settings("https://model.test", SecretStr("fake"), "test")
    )
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(database_path=path)) as client:
        client.post("/api/session", json={})
        token = client.cookies.get("proxy_session")
        cid = create(client)
        start(client, cid, content="질문")
        data = client.get(f"/api/conversations/{cid}").json()["messages"]
        assert data[-1]["status"] == "failed" and data[-1]["text"] == "중간"
        assert "private" not in str(data)
        aid = data[-1]["id"]
        start(client, cid, retry_message_id=aid)
        assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 2
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
            db.execute(
                "UPDATE runs SET status='streaming' WHERE rowid=(SELECT max(rowid) FROM runs)"
            )
            db.execute("UPDATE messages SET status='streaming' WHERE id=?", (aid,))
        assert start(client, cid, content="동시").status_code == 409
    with TestClient(create_app(database_path=path)) as client:
        client.cookies.set("proxy_session", token)
        last = client.get(f"/api/conversations/{cid}").json()["messages"][-1]
        assert last["status"] == "interrupted" and last["retryable"]
        assert start(client, cid, retry_message_id=aid).status_code == 200


def test_history_window_and_message_pages(tmp_path, monkeypatch):
    calls = []

    async def reply(messages, settings, call_context=None):
        calls.append(messages)
        yield "답"

    monkeypatch.setattr(routes, "stream_reply", reply)
    monkeypatch.setattr(
        routes, "load_settings", lambda: Settings("https://model.test", SecretStr("fake"), "test")
    )
    with TestClient(create_app(database_path=tmp_path / "db.sqlite3")) as client:
        client.post("/api/session", json={})
        cid = create(client)
        for i in range(42):
            assert start(client, cid, content=str(i)).status_code == 200
        assert len(calls[-1]) == 79 and calls[-1][0]["content"] == "2"
        first = client.get(f"/api/conversations/{cid}").json()
        second = client.get(
            f"/api/conversations/{cid}", params={"before": first["next_cursor"]}
        ).json()
        assert len(first["messages"]) == 50 and len(second["messages"]) == 34
        assert second["messages"][0]["text"] == "0"
        assert len({m["id"] for m in first["messages"] + second["messages"]}) == 84


def test_save_failure_never_reports_done(tmp_path, monkeypatch):
    async def reply(*args):
        yield "저장 대상"

    monkeypatch.setattr(routes, "stream_reply", reply)
    monkeypatch.setattr(
        routes, "load_settings", lambda: Settings("https://model.test", SecretStr("fake"), "test")
    )
    with TestClient(create_app(database_path=tmp_path / "db.sqlite3")) as client:
        client.post("/api/session", json={})
        cid = create(client)
        store = client.app.state.store
        save = store.save_run

        def fail_completed(owner, identity, text, status="streaming", code=None):
            if status == "completed":
                raise sqlite3.OperationalError("private disk path")
            return save(owner, identity, text, status, code)

        monkeypatch.setattr(store, "save_run", fail_completed)
        response = start(client, cid, content="질문")
        assert "event: done" not in response.text
        assert "storage_unavailable" in response.text and "private disk" not in response.text
        assert client.get(f"/api/conversations/{cid}").json()["messages"][-1]["status"] == "failed"


def test_concurrent_requests_reserve_only_one_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from agent_service.chat.schemas import ChatRequest
    from agent_service.storage import ConversationStore, StoreError

    store = ConversationStore(tmp_path / "db.sqlite3")
    store.initialize()
    owner = store.owner_for_token(store.issue_session())
    cid = str(uuid4())
    store.create_conversation(owner, cid)

    def reserve(_):
        try:
            return store.begin_run(
                owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content="동시")
            )
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "conversation_busy" in results
    assert len(store.get_conversation(owner, cid)["messages"]) == 2


def test_history_excludes_partial_and_keeps_latest_whole_turns():
    from agent_service.chat.history import model_history

    rows = []
    for i in range(5):
        rows.extend(
            [
                {"role": "user", "text": str(i) * 12000, "status": "completed"},
                {"role": "assistant", "text": "완료" * 5000, "status": "completed"},
            ]
        )
    rows.extend(
        [
            {"role": "user", "text": "마지막", "status": "completed"},
            {"role": "assistant", "text": "미완성", "status": "stopped"},
        ]
    )
    history = model_history(rows)
    assert history[-1] == {"role": "user", "content": "마지막"}
    assert len(history) == 5 and history[0]["content"].startswith("3")
    assert sum(len(m["content"]) for m in history) <= 60000
