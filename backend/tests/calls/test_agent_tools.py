import asyncio
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from test_call_store import add_user, spec
from test_manager import manager
from test_native_audio import until

from agent_service.calls.tools import CallContext, build_call_tools, call_history
from agent_service.chat.runtime import build_agent, stream_agent

pytestmark = pytest.mark.asyncio


async def test_tools_inject_ownership_and_return_without_waiting_for_phone(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    tools = build_call_tools(CallContext(m, o, c, u))
    assert {t.name for t in tools} == {
        "start_phone_call",
        "get_phone_call",
        "end_phone_call",
        "update_phone_call",
    }
    schema = tools[0].args_schema.model_json_schema()["properties"]
    assert not {"owner", "conversation_id", "source_user_message_id"} & schema.keys()
    accepted = await tools[0].ainvoke(spec().model_dump())
    assert accepted["call_id"]
    await until(lambda: g.dials == 1)
    assert not g.closed
    status = await tools[1].ainvoke({"call_id": accepted["call_id"]})
    assert status["id"] == accepted["call_id"]
    other = build_call_tools(CallContext(m, "other", c, u))
    assert (await other[1].ainvoke({"call_id": accepted["call_id"]}))["error"] == "not_found"
    await tools[2].ainvoke({"call_id": accepted["call_id"]})
    await m.wait_idle()
    assert g.hangups == 1


async def test_real_graph_calls_tool_twice_but_dials_once_and_keeps_chat_free(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": i,
                        "id": f"tool{i}",
                        "type": "function",
                        "function": {
                            "name": "start_phone_call",
                            "arguments": spec().model_dump_json(),
                        },
                    }
                    for i in range(2)
                ],
            }
            reason = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "전화를 연결하고 있습니다."}
            reason = "stop"
        chunks = [
            {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(
            model="test-chat",
            api_key="fake",
            base_url="https://model.test/v1",
            streaming=True,
            max_retries=0,
            http_async_client=client,
        )
        agent = build_agent(model, call_tools=build_call_tools(CallContext(m, o, c, u)))
        messages = [{"role": "user", "content": "01000000001로 전화해줘"}]
        result = "".join([t async for t in stream_agent(agent, messages)])
        assert "연결" in result
        await until(lambda: g.dials == 1)
        # A second text response runs while the same phone work is alive.
        assert "".join(
            [t async for t in stream_agent(agent, [{"role": "user", "content": "안녕"}])]
        )
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {
        "write_todos",
        "start_phone_call",
        "get_phone_call",
        "end_phone_call",
        "update_phone_call",
    }
    outputs = [json.loads(v["content"]) for v in requests[1]["messages"] if v["role"] == "tool"]
    assert outputs[0]["call_id"] == outputs[1]["call_id"] and g.dials == 1
    await m.shutdown()


async def test_stored_call_data_is_bounded_and_unavailable_config_is_tool_error(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    ctx = CallContext(m, o, c, u)
    a = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    await m.stop(o, a["id"])
    await m.wait_idle()
    uid = add_user(db, o, c, "결과가 뭐야?")["user_message_id"]
    records = await call_history(CallContext(m, o, c, uid))
    assert len(records) < 7000 and "기록 데이터" in records and a["id"] in records
    tools = build_call_tools(ctx)
    from agent_service.calls.types import ProviderFailure

    def disabled():
        raise ProviderFailure("calls_not_configured")

    m.settings_loader = disabled
    assert (await tools[0].ainvoke(spec().model_dump()))["error"] == "calls_not_configured"
    assert g.dials == 1


async def test_chat_task_cancellation_after_acceptance_does_not_stop_call(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    tools = build_call_tools(CallContext(m, o, c, u))
    started = asyncio.Event()

    async def chat():
        await tools[0].ainvoke(spec().model_dump())
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(chat())
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await until(lambda: g.dials == 1)
    assert not g.closed
    await m.shutdown()
    assert g.hangups == 1


async def test_main_chat_receives_confirmed_carrier_reason(tmp_path):
    m, g, db, s, o, c, u = manager(tmp_path)
    g.status = "no_answer"
    await m.start(o, c, u, spec())
    await m.wait_idle()
    records = await call_history(CallContext(m, o, c, u))
    saved = json.loads(records.split("\n", 1)[1])
    assert saved[0]["error_code"] == "call_no_answer"
    assert saved[0]["status"] == "ended"
    assert "destination" not in saved[0]
