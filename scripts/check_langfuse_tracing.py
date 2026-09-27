#!/usr/bin/env python3
"""Explicit synthetic graph smoke test. No real model, Google API, or phone calls."""

import argparse
import asyncio
import json
from uuid import uuid4

import httpx
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from agent_service.chat.runtime import build_agent, stream_agent
from agent_service.observability import (
    get_trace_client,
    load_trace_settings,
    shutdown_tracing,
    trace_execution,
    trace_id,
)


async def run():
    settings = load_trace_settings()
    client = get_trace_client()
    if settings is None or client is None:
        raise ValueError("local_tracing_not_configured")
    source, conversation = str(uuid4()), str(uuid4())

    @tool
    async def synthetic_lookup() -> dict:
        """Return a synthetic calendar result without any external operation."""
        return {"available": True, "private": "PRIVATE_TOOL_MARKER"}

    async def handler(request):
        done = any(m["role"] == "tool" for m in json.loads(request.content)["messages"])
        delta = (
            {"role": "assistant", "content": "PRIVATE_ANSWER_MARKER"}
            if done
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "test1",
                        "type": "function",
                        "function": {
                            "name": "synthetic_lookup",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        )
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": d, "finish_reason": r},
                ],
            }
            for d, r in [(delta, None), ({}, "stop" if done else "tool_calls")]
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        model = ChatOpenAI(
            model="synthetic-model",
            api_key="PRIVATE_KEY_MARKER",
            base_url="https://synthetic.invalid/v1",
            http_async_client=mock,
            streaming=True,
            max_retries=0,
        )
        for kind in ("main-chat", "call-delegation"):
            with trace_execution(
                kind,
                "synthetic-model",
                ["synthetic_lookup"],
                identity=(conversation, source, "synthetic-call"),
            ) as callbacks:
                result = "".join(
                    [
                        s
                        async for s in stream_agent(
                            build_agent(model, call_tools=[synthetic_lookup]),
                            [{"role": "user", "content": "PRIVATE_PROMPT_MARKER"}],
                            callbacks=callbacks,
                        )
                    ]
                )
                assert result == "PRIVATE_ANSWER_MARKER"
    await asyncio.to_thread(client.flush)
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        auth=(settings.public_key, settings.secret_key),
        trust_env=False,
        timeout=5,
    ) as http:
        rows = []
        for _ in range(30):
            response = await http.get(
                "/api/public/v2/observations",
                params={
                    "traceId": trace_id(source),
                    "limit": 100,
                    "fields": "basic,io,metadata,model,trace_context",
                },
            )
            response.raise_for_status()
            rows = response.json()["data"]
            if len(rows) == 8:
                break
            await asyncio.sleep(1)
    assert len(rows) == 8, "missing_observations"
    assert all(r.get("endTime") for r in rows), "unclosed_observations"
    assert all(not r.get("input") and not r.get("output") for r in rows), "unexpected_io"
    assert "PRIVATE_" not in json.dumps(rows), "private_payload_exported"
    roots = [r for r in rows if r["type"] == "AGENT"]
    assert {r["name"] for r in roots} == {"main-chat", "call-delegation"}
    assert all(r.get("sessionId") == trace_id(conversation) for r in roots)
    for root in roots:
        children = [r for r in rows if r.get("parentObservationId") == root["id"]]
        assert sorted(r["type"] for r in children) == ["GENERATION", "GENERATION", "TOOL"]
    return {
        "ok": True,
        "trace_id": trace_id(source),
        "observations": len(rows),
        "model_calls": 4,
        "tool_calls": 2,
        "raw_payloads_absent": True,
        "real_phone_or_model_called": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run())))
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    finally:
        shutdown_tracing()
