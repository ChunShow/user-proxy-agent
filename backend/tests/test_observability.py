import json
from uuid import uuid4

import pytest

from agent_service.observability import SafeCallback, load_trace_settings, trace_id


class Span:
    def __init__(self, records, **kwargs):
        self.records = records
        self.data = kwargs
        self.ended = False
        records.append(self)

    def start_observation(self, **kwargs):
        return Span(self.records, **kwargs)

    def update(self, **kwargs):
        self.data.update(kwargs)

    def end(self):
        self.ended = True


def test_settings_opt_in_and_local_only(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "LANGFUSE_BASE_URL=http://localhost:3000\nLANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n"
    )
    assert load_trace_settings(p, environ={}) is None
    p.write_text(p.read_text() + "LANGFUSE_TRACING_ENABLED=1\n")
    settings = load_trace_settings(p, environ={})
    assert settings.base_url == "http://localhost:3000"
    assert "sk" not in repr(settings)
    assert load_trace_settings(p, environ={"LANGFUSE_BASE_URL": "https://external.example"}) is None
    assert load_trace_settings(p, environ={"LANGFUSE_TRACING_ENABLED": "0"}) is None


def test_correlation_is_stable_without_plain_identifiers():
    assert trace_id("message-a") == trace_id("message-a")
    assert trace_id("message-a") != trace_id("message-b")
    assert len(trace_id("message-a")) == 32


@pytest.mark.asyncio
async def test_callbacks_record_names_status_and_never_payloads():
    records = []
    callback = SafeCallback(Span(records, name="root"), "test-model", {"read_email"})
    model, tool = uuid4(), uuid4()
    await callback.on_chat_model_start({"secret": "private"}, [["private"]], run_id=model)
    await callback.on_llm_end({"private": "private"}, run_id=model)
    await callback.on_tool_start({"name": "read_email"}, "private", run_id=tool)
    await callback.on_tool_end({"error": "private-error", "body": "private"}, run_id=tool)
    assert [x.data["name"] for x in records] == ["root", "test-model", "read_email"]
    assert records[1].data["metadata"]["status"] == "completed"
    assert records[2].data["metadata"]["status"] == "error"
    assert "private" not in json.dumps([x.data for x in records])
    assert all(x.ended for x in records[1:])


@pytest.mark.asyncio
async def test_parallel_same_name_errors_and_cancellation():
    records = []
    callback = SafeCallback(Span(records, name="root"), "test-model", {"read_email"})
    a, b = uuid4(), uuid4()
    await callback.on_tool_start({"name": "read_email"}, "secret", run_id=a)
    await callback.on_tool_start({"name": "read_email"}, "secret", run_id=b)
    await callback.on_tool_error(ValueError("private-secret"), run_id=a)
    callback.close("canceled")
    assert [s.data["metadata"]["status"] for s in records[1:]] == ["error", "canceled"]
    assert all(s.ended for s in records)
    assert "private-secret" not in json.dumps([s.data for s in records])


@pytest.mark.asyncio
async def test_observability_failure_never_raises():
    class Broken:
        def start_observation(self, **kw):
            raise RuntimeError("private")

        def update(self, **kw):
            raise RuntimeError("private")

        def end(self):
            raise RuntimeError("private")

    callback = SafeCallback(Broken(), "model", {"read_email"})
    run = uuid4()
    await callback.on_tool_start({"name": "read_email"}, "private", run_id=run)
    await callback.on_tool_end({"ok": True}, run_id=run)
    callback.close("completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["main-chat", "call-delegation"])
async def test_real_graph_entrypoints_trace_model_tool_and_share_request(monkeypatch, mode):
    from types import SimpleNamespace

    import httpx
    from langchain_core.tools import tool

    from agent_service import observability as obs
    from agent_service.calls import delegation
    from agent_service.chat import runtime

    records = []
    client = Span(records, name="client")
    monkeypatch.setattr(obs, "get_trace_client", lambda: client)

    @tool
    async def test_lookup() -> dict:
        """Read synthetic test data."""
        return {"available": True, "private": "PRIVATE_TOOL_DATA"}

    async def http_handler(request):
        messages = json.loads(request.content)["messages"]
        done = any(m["role"] == "tool" for m in messages)
        delta = (
            {"role": "assistant", "content": "PRIVATE_ANSWER"}
            if done
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "lookup1",
                        "type": "function",
                        "function": {"name": "test_lookup", "arguments": "{}"},
                    }
                ],
            }
        )
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": d, "finish_reason": r}],
            }
            for d, r in [(delta, None), ({}, "stop" if done else "tool_calls")]
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    original = httpx.AsyncClient

    class MockClient(original):
        def __init__(self, **kw):
            super().__init__(**kw, transport=httpx.MockTransport(http_handler))

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    settings = SimpleNamespace(
        model_name="test-model",
        base_url="https://model.test/v1",
        api_key="PRIVATE_KEY",
        trust_env=False,
        max_tokens=100,
    )
    identity = ("conversation-id", "message-id", "call-id")
    if mode == "main-chat":
        context = SimpleNamespace(
            conversation_id=identity[0],
            source_user_message_id=identity[1],
            manager=SimpleNamespace(),
        )

        async def empty_history(context):
            return ""

        monkeypatch.setattr(runtime, "call_history", empty_history)
        monkeypatch.setattr(runtime, "build_call_tools", lambda _: [test_lookup])
        result = "".join(
            [
                s
                async for s in runtime.stream_reply(
                    [{"role": "user", "content": "PRIVATE_PROMPT"}], settings, context
                )
            ]
        )
    else:
        monkeypatch.setattr(delegation, "load_settings", lambda: settings)
        with obs.correlation(*identity):
            result = await delegation.run_delegation({"prompt": "PRIVATE_PROMPT"}, [test_lookup])
    assert result == "PRIVATE_ANSWER"
    root = records[1]
    assert root.data["name"] == mode
    assert root.data["trace_context"]["trace_id"] == trace_id(identity[1])
    assert [s.data["as_type"] for s in records[2:]] == ["generation", "tool", "generation"]
    assert records[3].data["name"] == "test_lookup"
    assert all(s.ended for s in records[1:])
    assert "PRIVATE" not in json.dumps([s.data for s in records])


def test_trace_root_closes_on_failure_and_cancel(monkeypatch):
    import asyncio

    from agent_service import observability as obs

    records = []
    monkeypatch.setattr(obs, "get_trace_client", lambda: Span(records, name="client"))
    for error, status in [(ValueError("private"), "error"), (asyncio.CancelledError(), "canceled")]:
        with pytest.raises(type(error)):
            with obs.trace_execution("main-chat", "model", []):
                raise error
        assert records[-1].ended
        assert records[-1].data["metadata"]["status"] == status


@pytest.mark.asyncio
async def test_context_isolated_between_concurrent_tasks():
    import asyncio

    from agent_service import observability as obs

    async def task(source):
        with obs.correlation("conversation", source):
            await asyncio.sleep(0)
            return obs._identity.get()[1]

    assert await asyncio.gather(task("a"), task("b")) == ["a", "b"]
    assert obs._identity.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("export_fails", [False, True])
async def test_sdk_export_is_metadata_only_and_failure_does_not_break_work(
    monkeypatch, export_fails
):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    from agent_service import observability as obs

    exported = []

    class Exporter(SpanExporter):
        def export(self, spans):
            exported.extend(spans)
            return SpanExportResult.FAILURE if export_fails else SpanExportResult.SUCCESS

    client = Langfuse(
        public_key="pk-test-" + str(uuid4()),
        secret_key="PRIVATE_SECRET",
        base_url="http://127.0.0.1:1",
        tracer_provider=TracerProvider(),
        span_exporter=Exporter(),
    )
    monkeypatch.setattr(obs, "get_trace_client", lambda: client)
    try:
        with obs.trace_execution("main-chat", "test-model", ["read_email"]) as callbacks:
            callback = callbacks[0]
            run = uuid4()
            await callback.on_tool_start({"name": "read_email"}, "PRIVATE_INPUT", run_id=run)
            await callback.on_tool_end({"body": "PRIVATE_OUTPUT"}, run_id=run)
        client.flush()
        assert len(exported) == 2
        attributes = json.dumps([dict(span.attributes) for span in exported])
        assert "PRIVATE_" not in attributes
        assert all(span.end_time is not None for span in exported)
    finally:
        client.shutdown()
