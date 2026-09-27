import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_call_store import add_user, setup_store, spec

from agent_service.storage import StoreError


def setup_report(tmp_path):
    from agent_service.calls.report_store import CallReportStore

    db, calls, owner, cid, uid = setup_store(tmp_path)
    call = calls.register(owner, cid, uid, spec())["id"]
    return db, calls, CallReportStore(calls), owner, cid, call


def test_terminal_transition_queues_once_and_completion_is_atomic(tmp_path):
    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    assert reports.claim() is None
    calls.update(call, status="ending")
    assert reports.claim() is None
    calls.update(call, status="ended")
    calls.update(call, status="ended")
    assert reports.claim()["id"] == call
    assert reports.claim() is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        identities = list(pool.map(lambda _: reports.complete(call, "통화 결과입니다."), range(2)))
    assert identities[0] == identities[1]
    messages = db.get_conversation(owner, cid)["messages"]
    assert [m["text"] for m in messages].count("통화 결과입니다.") == 1
    assert messages[-1]["status"] == "completed"
    assert calls.get(owner, call)["result_message_id"] == identities[0]
    with pytest.raises(StoreError):
        calls.get("other", call)
    db.initialize()
    assert reports.claim() is None
    assert db.get_conversation(owner, cid)["messages"] == messages


def test_old_terminal_rows_are_not_backfilled_and_unknown_never_reports(tmp_path):
    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    with db.connection() as sql:
        sql.execute("UPDATE phone_calls SET status='ended' WHERE id=?", (call,))
    db.initialize()
    assert reports.claim() is None
    next_call = calls.register(owner, cid, add_user(db, owner, cid)["user_message_id"], spec())[
        "id"
    ]
    calls.update(next_call, status="unknown")
    assert reports.claim() is None


def test_report_and_user_message_sequence_do_not_collide(tmp_path):
    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    calls.update(call, status="ended")
    reports.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(reports.complete, call, "완료 보고"),
            pool.submit(add_user, db, owner, cid, "다음 질문"),
        ]
        for task in tasks:
            task.result()
    with db.connection() as sql:
        seqs = [
            r[0]
            for r in sql.execute(
                "SELECT seq FROM messages WHERE conversation_id=? ORDER BY seq", (cid,)
            )
        ]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(db.get_conversation(owner, cid)["messages"]) == 5


@pytest.mark.asyncio
async def test_slow_report_does_not_hold_call_and_is_stored_once(tmp_path):
    from agent_service.calls.reports import CallReportManager

    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(context):
        assert context["call"]["status"] == "ended"
        assert context["transcript"]["listener_hearing_verified"] is False
        started.set()
        await release.wait()
        return "상대방 답변을 확인했습니다."

    manager = CallReportManager(calls, generator=generate)
    calls.update(call, status="ended")
    task = asyncio.create_task(manager.run_once())
    await asyncio.wait_for(started.wait(), 2)
    assert calls.get(owner, call)["status"] == "ended"
    assert calls.get(owner, call)["result_message_id"] is None
    release.set()
    assert await task is True
    assert await manager.run_once() is False
    assert db.get_conversation(owner, cid)["messages"][-1]["text"].endswith(
        "상대방 답변을 확인했습니다."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "empty", "timeout"])
async def test_generation_failure_leaves_a_saved_notice_without_retry(tmp_path, mode):
    from agent_service.calls.reports import CallReportManager

    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    attempts = []

    async def generate(context):
        attempts.append(1)
        if mode == "error":
            raise RuntimeError("private error")
        if mode == "timeout":
            await asyncio.Event().wait()
        return ""

    manager = CallReportManager(calls, generator=generate, timeout=0.03)
    calls.update(call, status="failed")
    await manager.run_once()
    await manager.run_once()
    text = db.get_conversation(owner, cid)["messages"][-1]["text"]
    assert "요약을 만들지 못했습니다" in text and "연결하지 못했습니다" in text
    assert "private error" not in text and attempts == [1]


@pytest.mark.asyncio
async def test_restart_and_shutdown_finish_running_reports_without_regeneration(tmp_path):
    from agent_service.calls.reports import CallReportManager

    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    calls.update(call, status="canceled")
    reports.claim()

    async def forbidden(context):
        pytest.fail("interrupted report must not invoke the model again")

    manager = CallReportManager(calls, generator=forbidden, poll_seconds=0.01)
    await manager.start()
    await manager.shutdown()
    assert "취소" in db.get_conversation(owner, cid)["messages"][-1]["text"]
    assert calls.get(owner, call)["result_message_id"]
    await manager.start()
    await manager.shutdown()
    assert len(db.get_conversation(owner, cid)["messages"]) == 3


@pytest.mark.asyncio
async def test_queued_work_resumes_and_shutdown_during_generation_publishes_fallback(tmp_path):
    from agent_service.calls.reports import CallReportManager

    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    calls.update(call, status="ended")
    db.initialize()
    started = asyncio.Event()

    async def slow(context):
        started.set()
        await asyncio.Event().wait()

    manager = CallReportManager(calls, generator=slow)
    await manager.start()
    await asyncio.wait_for(started.wait(), 2)
    await manager.shutdown()
    assert calls.get(owner, call)["result_message_id"]
    assert "요약을 만들지 못했습니다" in db.get_conversation(owner, cid)["messages"][-1]["text"]
    assert reports.claim() is None


@pytest.mark.asyncio
async def test_report_generation_uses_no_tools_and_traces_original_call(monkeypatch):
    import json
    from contextlib import contextmanager

    import httpx
    from pydantic import SecretStr

    from agent_service.calls import reports
    from agent_service.settings import Settings

    requests, traces = [], []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "확인된 합성 결과입니다."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    @contextmanager
    def trace(kind, model, tools, *, identity):
        traces.append((kind, tools, identity))
        yield []

    from types import SimpleNamespace

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        reports,
        "load_settings",
        lambda: Settings("https://model.test/v1", SecretStr("synthetic"), "test", 2000, False),
    )
    monkeypatch.setattr(reports, "trace_execution", trace)
    monkeypatch.setattr(
        reports,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler), **kwargs
            )
        ),
    )
    result = await reports.generate_report(
        {"identity": ("conversation", "source", "call"), "call": {"status": "ended"}}
    )
    assert result == "확인된 합성 결과입니다."
    assert len(requests) == 1 and not requests[0].get("tools")
    assert traces == [("call-result", [], ("conversation", "source", "call"))]
    assert "identity" not in json.loads(requests[0]["messages"][-1]["content"])


@pytest.mark.asyncio
async def test_structured_response_uses_only_visible_text(monkeypatch):
    from contextlib import nullcontext

    from langchain_core.messages import AIMessage

    from agent_service.calls import reports

    class Model:
        def __init__(self, **kwargs):
            pass

        async def ainvoke(self, *args, **kwargs):
            return AIMessage(content=[
                {"type": "reasoning", "text": "비공개 추론"},
                {"type": "text", "text": "오후 6시가 가능합니다."},
            ])

    from types import SimpleNamespace
    monkeypatch.setattr(reports, "ChatOpenAI", Model)
    monkeypatch.setattr(reports, "load_settings", lambda: SimpleNamespace(
        trust_env=False, model_name="synthetic", base_url="https://model.test/v1",
        api_key="synthetic", max_tokens=2000,
    ))
    monkeypatch.setattr(reports, "trace_execution", lambda *a, **k: nullcontext([]))
    assert await reports.generate_report({"identity": ("c", "u", "p")}) == "오후 6시가 가능합니다."


def test_report_does_not_block_retry_of_latest_chat_reply(tmp_path):
    from uuid import uuid4

    from agent_service.chat.schemas import ChatRequest

    db, calls, reports, owner, cid, call = setup_report(tmp_path)
    reply = db.get_conversation(owner, cid)["messages"][-1]
    with db.connection() as sql:
        sql.execute("UPDATE messages SET status='stopped' WHERE id=?", (reply["id"],))
    calls.update(call, status="ended")
    reports.claim()
    report_id = reports.complete(call, "통화 결과입니다.")
    messages = db.get_conversation(owner, cid)["messages"]
    assert messages[-1]["kind"] == "call_result"
    identity = db.begin_run(owner, ChatRequest(
        request_id=uuid4(), conversation_id=cid, retry_message_id=reply["id"],
    ))
    assert identity["history"][-1]["role"] == "user"
    db.save_run(owner, identity, "다시 작성했습니다.", "completed")
    messages = db.get_conversation(owner, cid)["messages"]
    assert messages[-1]["id"] == report_id
    assert messages[-2]["text"] == "다시 작성했습니다."
    add_user(db, owner, cid, "새 질문")
    with pytest.raises(StoreError):
        db.begin_run(owner, ChatRequest(
            request_id=uuid4(), conversation_id=cid, retry_message_id=reply["id"],
        ))
