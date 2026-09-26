import httpx
import pytest
from test_queries import setup

from agent_service.integrations.tools import build_integration_tools


@pytest.mark.asyncio
async def test_main_tools_bind_owner_and_disallow_model_identity_or_url(tmp_path):
    requests = []

    def provider(r):
        requests.append(r)
        return httpx.Response(200, json={"items": []})

    g = setup(tmp_path, provider)
    tools = {t.name: t for t in build_integration_tools(g, "owner")}
    assert set(tools) == {
        "get_connected_apps",
        "list_calendars",
        "list_calendar_events",
        "search_email",
        "read_email",
    }
    for t in tools.values():
        assert (
            not {"owner", "token", "url"} & t.args_schema.model_json_schema()["properties"].keys()
        )
    result = await tools["list_calendar_events"].ainvoke(
        {"start": "2026-09-27T00:00:00+09:00", "end": "2026-09-28T00:00:00+09:00"}
    )
    assert result["items"] == [] and result["source"] == "Google Calendar"
    other = {t.name: t for t in build_integration_tools(g, "other")}
    assert (await other["list_calendars"].ainvoke({}))["error"] == "integration_not_connected"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_deepagents_queries_real_scoped_tool_with_mock_provider(tmp_path):
    import json

    from langchain_openai import ChatOpenAI

    from agent_service.chat.runtime import build_agent, stream_agent

    g = setup(
        tmp_path,
        lambda r: httpx.Response(
            200, json={"items": [{"id": "primary", "summary": "My calendar", "primary": True}]}
        ),
    )
    requests = []

    async def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "list-1",
                        "type": "function",
                        "function": {"name": "list_calendars", "arguments": "{}"},
                    }
                ],
            }
            reason = "tool_calls"
        else:
            result = json.loads(next(m["content"] for m in data["messages"] if m["role"] == "tool"))
            assert result["items"][0]["summary"] == "My calendar"
            assert result["untrusted_external_data"] is True
            delta = {"role": "assistant", "content": "캘린더를 확인했어요."}
            reason = "stop"
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": d, "finish_reason": r}],
            }
            for d, r in [(delta, None), ({}, reason)]
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(
            model="test",
            api_key="fake",
            base_url="https://model.test/v1",
            streaming=True,
            max_retries=0,
            http_async_client=client,
        )
        agent = build_agent(model, call_tools=build_integration_tools(g, "owner"))
        assert "확인" in "".join(
            [
                text
                async for text in stream_agent(
                    agent, [{"role": "user", "content": "캘린더를 확인해줘"}]
                )
            ]
        )
    assert len(requests) == 2
    assert "TOKEN" not in json.dumps(requests)


@pytest.mark.asyncio
async def test_call_calendar_no_connection_or_stale_conditions_requests_user(tmp_path):
    from agent_service.integrations.tools import build_delegation_app_tools

    g = setup(tmp_path, lambda r: httpx.Response(200, json={"items": []}))

    async def valid():
        return True

    tool = build_delegation_app_tools(g, "other", valid)[0]
    args = {"start": "2026-09-27T00:00:00Z", "end": "2026-09-28T00:00:00Z"}
    result = await tool.ainvoke(args)
    assert result["ask_requesting_user"] and not result["confirmed"]

    async def stale():
        return False

    tool = build_delegation_app_tools(g, "owner", stale)[0]
    assert (await tool.ainvoke(args))["error"] == "call_question_inactive"
