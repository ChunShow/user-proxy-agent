import json

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI


def test_only_user_visible_text_is_extracted():
    from agent_service.chat.runtime import visible_text

    assert (
        visible_text(
            AIMessageChunk(
                content=[
                    {"type": "reasoning", "reasoning": "private"},
                    {"type": "text", "text": "한국어 응답"},
                ]
            )
        )
        == "한국어 응답"
    )
    assert (
        visible_text(
            AIMessageChunk(
                content="not shown",
                tool_call_chunks=[
                    {
                        "name": "write_todos",
                        "args": "{}",
                        "id": "call1",
                        "index": 0,
                    }
                ],
            )
        )
        == ""
    )


@pytest.mark.asyncio
async def test_real_deepagents_graph_sends_only_allowed_tools_and_streams_text():
    from agent_service.chat.runtime import build_agent, stream_agent

    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        events = [
            {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "안녕"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "하세요"}, "finish_reason": "stop"}],
            },
        ]
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(
            model="test-chat",
            api_key="fake",
            base_url="https://model.test/v1",
            http_async_client=client,
            streaming=True,
            max_retries=0,
        )
        agent = build_agent(model)
        text = "".join(
            [s async for s in stream_agent(agent, [{"role": "user", "content": "안녕"}])]
        )
    assert text == "안녕하세요"
    assert len(requests) == 1
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {"write_todos"}
    assert requests[0]["messages"][-1]["content"] == "안녕"
