import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from agent_service.calls.media import MediaProtocol


class Socket:
    def __init__(self):
        self.input = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def recv(self):
        value = await self.input.get()
        if value is None:
            raise EOFError
        return json.dumps(value)

    async def send(self, message):
        self.sent.append(json.loads(message))

    @asynccontextmanager
    async def connect(self, *args, **kwargs):
        self.options = kwargs
        try:
            yield self
        finally:
            self.closed = True


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def phone_start():
    return {
        "event": "start",
        "start": {
            "accountId": "ACtest",
            "callId": "CAtest",
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    }


def delta(response="r1", item="i1", data=b"\xff" * 160):
    return {
        "type": "response.output_audio.delta",
        "response_id": response,
        "item_id": item,
        "content_index": 0,
        "delta": base64.b64encode(data).decode(),
    }


@pytest.mark.asyncio
async def test_native_session_configures_audio_without_transcription_or_text_output():
    from agent_service.calls.realtime import AzureAudioSession

    model = Socket()
    await model.input.put({"type": "session.created"})
    await model.input.put(
        {
            "type": "session.updated",
            "session": {
                "output_modalities": ["audio"],
                "audio": {
                    "input": {"format": {"type": "audio/pcmu"}, "transcription": None},
                    "output": {"format": {"type": "audio/pcmu"}},
                },
            },
        }
    )
    async with AzureAudioSession(
        "https://test.openai.azure.com",
        "secret",
        "deployment",
        connector=model.connect,
        task="병원 위치 안내를 확인하세요.",
    ):
        settings = model.sent[0]["session"]
        assert settings["output_modalities"] == ["audio"]
        assert settings["audio"]["input"]["format"] == {"type": "audio/pcmu"}
        assert settings["audio"]["output"]["format"] == {"type": "audio/pcmu"}
        assert settings["audio"]["input"]["transcription"] is None
        assert settings["audio"]["input"]["turn_detection"]["interrupt_response"]
        assert settings["tool_choice"] == "auto"
        assert {tool["name"] for tool in settings["tools"]} == {"end_call", "send_dtmf"}
        assert "병원 위치 안내를 확인하세요." in settings["instructions"]
    assert model.closed


@pytest.mark.asyncio
async def test_bridge_passes_phone_audio_unchanged_and_plays_model_audio():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    media = NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))
    bridge = NativeAudioBridge(model)
    task = asyncio.create_task(bridge.run(media))
    try:
        await phone.input.put(phone_start())
        payload = base64.b64encode(bytes(range(160))).decode()
        await phone.input.put({"event": "media", "media": {"payload": payload}})
        await until(lambda: any(x["type"] == "input_audio_buffer.append" for x in model.sent))
        assert (
            next(x["audio"] for x in model.sent if x["type"] == "input_audio_buffer.append")
            == payload
        )
        await model.input.put({"type": "response.created", "response": {"id": "r1"}})
        await model.input.put(delta())
        await model.input.put(
            {
                "type": "response.output_audio.done",
                "response_id": "r1",
                "item_id": "i1",
                "content_index": 0,
            }
        )
        await until(lambda: any(x["event"] == "mark" for x in phone.sent))
        assert (
            next(x["media"]["payload"] for x in phone.sent if x["event"] == "media")
            == base64.b64encode(b"\xff" * 160).decode()
        )
        mark = next(x for x in phone.sent if x["event"] == "mark")
        await phone.input.put(mark)
        await until(lambda: bridge.report()["played_audio_ms"] == 20)
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        result = await task
        assert result["status"] == "completed" and result["input_audio_bytes"] == 160
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_barge_in_clears_audio_truncates_unplayed_history_and_ignores_late_delta():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    bridge = NativeAudioBridge(model)
    task = asyncio.create_task(bridge.run(NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))))
    try:
        await phone.input.put(phone_start())
        await model.input.put({"type": "response.created", "response": {"id": "r1"}})
        await model.input.put(delta(data=b"\xff" * 800))
        await until(lambda: any(x["event"] == "media" for x in phone.sent))
        await model.input.put({"type": "input_audio_buffer.speech_started"})
        await until(lambda: any(x["type"] == "conversation.item.truncate" for x in model.sent))
        cut = next(x for x in model.sent if x["type"] == "conversation.item.truncate")
        assert cut["item_id"] == "i1" and cut["audio_end_ms"] == 0
        assert any(x["event"] == "clear" for x in phone.sent)
        await model.input.put(delta(data=b"\x80" * 160))
        await model.input.put({"type": "response.created", "response": {"id": "r2"}})
        await model.input.put(delta("r2", "i2", b"\x7f" * 160))
        await model.input.put(
            {
                "type": "response.output_audio.done",
                "response_id": "r2",
                "item_id": "i2",
                "content_index": 0,
            }
        )
        await until(
            lambda: any(
                x.get("media", {}).get("payload") == base64.b64encode(b"\x7f" * 160).decode()
                for x in phone.sent
            )
        )
        assert not any(
            x.get("media", {}).get("payload") == base64.b64encode(b"\x80" * 160).decode()
            for x in phone.sent
        )
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_truncation_keeps_only_acknowledged_audio_not_sent_audio():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    bridge = NativeAudioBridge(model)
    task = asyncio.create_task(bridge.run(NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))))
    try:
        await phone.input.put(phone_start())
        await model.input.put({"type": "response.created", "response": {"id": "r1"}})
        await model.input.put(delta(data=b"\xff" * 3200))
        await until(lambda: any(x["event"] == "mark" for x in phone.sent))
        first_mark = next(x for x in phone.sent if x["event"] == "mark")
        await phone.input.put(first_mark)
        await until(lambda: bridge.report()["played_audio_ms"] == 100)
        await model.input.put({"type": "input_audio_buffer.speech_started"})
        await until(lambda: any(x["type"] == "conversation.item.truncate" for x in model.sent))
        cut = next(x for x in model.sent if x["type"] == "conversation.item.truncate")
        assert cut["audio_end_ms"] == 100
        await phone.input.put(
            first_mark
        )  # Delayed old receipt must not restore interrupted output.
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        result = await task
        assert result["ledger"][0]["status"] == "interrupted"
        assert result["played_audio_ms"] == 100
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_invalid_call_identity_ends_bridge_without_forwarding_audio():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    event = phone_start()
    event["start"]["callId"] = "CAother"
    await phone.input.put(event)
    result = await NativeAudioBridge(model).run(
        NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))
    )
    assert result["status"] == "failed"
    assert model.sent == []


@pytest.mark.asyncio
async def test_output_token_limit_plays_queued_audio_and_keeps_call_open_for_next_turn():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    bridge = NativeAudioBridge(model)
    task = asyncio.create_task(bridge.run(NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))))
    try:
        await phone.input.put(phone_start())
        await model.input.put({"type": "response.created", "response": {"id": "r1"}})
        await model.input.put(delta(data=b"\xff" * 1600))
        await model.input.put(
            {
                "type": "response.output_audio.done",
                "response_id": "r1",
                "item_id": "i1",
                "content_index": 0,
            }
        )
        await model.input.put(
            {
                "type": "response.done",
                "response": {
                    "id": "r1",
                    "status": "incomplete",
                    "status_details": {"type": "incomplete", "reason": "max_output_tokens"},
                    "usage": {"output_tokens": 500},
                    "output": [],
                },
            }
        )
        await until(
            lambda: task.done() or len([x for x in phone.sent if x["event"] == "media"]) == 10
        )
        assert not task.done(), "A response token limit must not terminate the telephone call"
        assert len([x for x in phone.sent if x["event"] == "media"]) == 10
        # Subsequent input is still forwarded and can receive a fresh answer.
        await phone.input.put(
            {"event": "media", "media": {"payload": base64.b64encode(b"\x7f" * 160).decode()}}
        )
        await until(lambda: any(x["type"] == "input_audio_buffer.append" for x in model.sent))
        await model.input.put({"type": "response.created", "response": {"id": "r2"}})
        await model.input.put(delta("r2", "i2", b"\x80" * 160))
        await model.input.put(
            {
                "type": "response.output_audio.done",
                "response_id": "r2",
                "item_id": "i2",
                "content_index": 0,
            }
        )
        await until(lambda: len([x for x in phone.sent if x["event"] == "media"]) == 11)
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        result = await task
        assert result["status"] == "completed"
        assert result["model_responses"][0]["reason"] == "max_output_tokens"
        assert result["model_responses"][0]["output_tokens"] == 500
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_nonrecoverable_failure_keeps_diagnostic_code_but_not_vendor_message():
    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import NativeMedia

    phone, model = Socket(), Socket()
    await phone.input.put(phone_start())
    await model.input.put(
        {
            "type": "response.done",
            "response": {
                "id": "r1",
                "status": "failed",
                "status_details": {
                    "type": "failed",
                    "error": {
                        "type": "server_error",
                        "code": "server_error",
                        "message": "private-key secret text",
                    },
                },
                "output": [],
            },
        }
    )
    result = await NativeAudioBridge(model).run(
        NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))
    )
    assert result["status"] == "failed"
    assert result["model_responses"][0]["error_code"] == "server_error"
    assert result["model_responses"][0]["status"] == "failed"
    assert "private-key" not in json.dumps(result)
