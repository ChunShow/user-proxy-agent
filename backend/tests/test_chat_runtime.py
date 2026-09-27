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


@pytest.mark.asyncio
async def test_closing_agent_stream_closes_the_provider_connection():
    import asyncio

    from agent_service.chat.runtime import build_agent, stream_agent

    closed = asyncio.Event()

    class ProviderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            data = {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "첫 조각"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(data)}\n\n".encode()
            await asyncio.sleep(300)

        async def aclose(self):
            closed.set()

    async def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ProviderStream()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(
            model="test-chat",
            api_key="fake",
            base_url="https://model.test/v1",
            http_async_client=client,
            streaming=True,
            max_retries=0,
        )
        stream = stream_agent(build_agent(model), [{"role": "user", "content": "시작"}])
        assert await anext(stream) == "첫 조각"
        await stream.aclose()
        await asyncio.wait_for(closed.wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_after", [6, None])
async def test_multi_read_graph_completes_but_repeated_tools_are_bounded(finish_after):
    """Six sequential reads must finish; an endless tool loop must still fail."""
    import asyncio

    from langchain_core.tools import tool
    from langgraph.errors import GraphRecursionError

    from agent_service.chat.runtime import build_agent, stream_agent

    @tool
    async def read_record(index: int) -> str:
        """Read one test record."""
        return f"record {index}"

    async def handler(request):
        messages = json.loads(request.content)["messages"]
        results = [m["content"] for m in messages if m["role"] == "tool"]
        if finish_after is not None and len(results) == finish_after:
            delta = {"role": "assistant", "content": "읽기 완료: " + ", ".join(results)}
            reason = "stop"
        else:
            index = len(results) + 1
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"read-{index}",
                        "type": "function",
                        "function": {
                            "name": "read_record",
                            "arguments": json.dumps({"index": index}),
                        },
                    }
                ],
            }
            reason = "tool_calls"
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
            model="test-chat",
            api_key="fake",
            base_url="https://model.test/v1",
            streaming=True,
            max_retries=0,
            http_async_client=client,
        )
        agent = build_agent(model, call_tools=[read_record])

        async def run():
            return "".join(
                [
                    s
                    async for s in stream_agent(
                        agent, [{"role": "user", "content": "레코드 여섯 개 읽어줘"}]
                    )
                ]
            )

        if finish_after is None:
            with pytest.raises(GraphRecursionError):
                await asyncio.wait_for(run(), timeout=10)
        else:
            assert (
                await run()
                == "읽기 완료: record 1, record 2, record 3, record 4, record 5, record 6"
            )
