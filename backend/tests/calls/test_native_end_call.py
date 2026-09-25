import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from test_native_audio import Socket, delta, phone_start, until

from agent_service.calls.bridge import NativeAudioBridge
from agent_service.calls.media import MediaProtocol, NativeMedia


def tool_done(*, status="completed", arguments=None, response="tool", call="end1"):
    return {
        "type": "response.done",
        "response": {
            "id": response,
            "status": status,
            "output": [
                {
                    "type": "function_call",
                    "status": "completed",
                    "name": "end_call",
                    "call_id": call,
                    "arguments": arguments
                    if arguments is not None
                    else json.dumps(
                        {
                            "reason": "goal_achieved",
                            "summary": "공주대학교 운동장으로 답변 재확인됨",
                        }
                    ),
                }
            ],
        },
    }


def farewells(model):
    return [
        e
        for e in model.sent
        if e["type"] == "response.create"
        and e.get("response", {}).get("metadata", {}).get("end_call_id")
    ]


async def request_end(model):
    await model.input.put({"type": "response.created", "response": {"id": "tool"}})
    await model.input.put(tool_done())
    await until(lambda: farewells(model))
    assert farewells(model)[0]["response"]["tool_choice"] == "none"


async def farewell_audio(model, *, completed=True):
    await model.input.put(
        {"type": "response.created", "response": {"id": "bye", "metadata": {"end_call_id": "end1"}}}
    )
    await model.input.put(delta("bye", "goodbye", b"\xff" * 1600))
    await model.input.put(
        {
            "type": "response.output_audio.done",
            "response_id": "bye",
            "item_id": "goodbye",
            "content_index": 0,
        }
    )
    if completed:
        await model.input.put(
            {
                "type": "response.done",
                "response": {"id": "bye", "status": "completed", "output": []},
            }
        )


@asynccontextmanager
async def running_bridge(**kwargs):
    phone, model = Socket(), Socket()
    bridge = NativeAudioBridge(model, **kwargs)
    task = asyncio.create_task(bridge.run(NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))))
    await phone.input.put(phone_start())
    try:
        yield phone, model, bridge, task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_end_call_needs_completed_farewell_response_as_well_as_playback():
    async with running_bridge() as (phone, model, bridge, task):
        await request_end(model)
        await farewell_audio(model, completed=False)
        await until(lambda: len([e for e in phone.sent if e["event"] == "mark"]) >= 3)
        await phone.input.put([e for e in phone.sent if e["event"] == "mark"][-1])
        await until(lambda: bridge.report()["played_audio_ms"] == 200)
        assert not task.done()
        await model.input.put(
            {
                "type": "response.done",
                "response": {"id": "bye", "status": "completed", "output": []},
            }
        )
        assert (await asyncio.wait_for(task, 2))["end_call"]["status"] == "played"


@pytest.mark.parametrize("before_created", [False, True])
@pytest.mark.asyncio
async def test_speech_cancels_pending_hangup_and_late_farewell_cannot_end_call(before_created):
    async with running_bridge() as (phone, model, bridge, task):
        await request_end(model)
        if not before_created:
            await farewell_audio(model)
            await until(lambda: any(e["event"] == "media" for e in phone.sent))
        await model.input.put({"type": "input_audio_buffer.speech_started"})
        await until(lambda: bridge.report().get("end_call", {}).get("status") == "interrupted")
        if before_created:
            await farewell_audio(model)
        for event in list(phone.sent):
            if event["event"] == "mark":
                await phone.input.put(event)
        await model.input.put({"type": "response.created", "response": {"id": "next"}})
        await model.input.put(delta("next", "answer"))
        await until(lambda: bridge.current == "answer")
        assert not task.done()
        assert any(e["event"] == "clear" for e in phone.sent)
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        assert (await task)["end_call"]["status"] == "interrupted"


@pytest.mark.parametrize("arguments", ["{", "[]", "{}", '{"reason":"guess","summary":"x"}'])
@pytest.mark.asyncio
async def test_invalid_end_call_arguments_do_not_end_or_break_conversation(arguments):
    async with running_bridge() as (phone, model, bridge, task):
        await model.input.put({"type": "response.created", "response": {"id": "tool"}})
        await model.input.put(tool_done(arguments=arguments))
        await until(lambda: any(e["type"] == "conversation.item.create" for e in model.sent))
        output = next(e["item"] for e in model.sent if e["type"] == "conversation.item.create")
        assert json.loads(output["output"])["error"] == "invalid_end_call_arguments"
        assert not farewells(model) and not task.done()


@pytest.mark.asyncio
async def test_cancelled_tool_response_cannot_request_hangup():
    async with running_bridge() as (phone, model, bridge, task):
        await model.input.put({"type": "response.created", "response": {"id": "tool"}})
        await model.input.put(tool_done(status="cancelled"))
        await until(lambda: bridge.model_responses)
        assert not farewells(model)
        assert bridge.report().get("end_call") is None
        assert not task.done()


@pytest.mark.parametrize("with_audio", [False, True])
@pytest.mark.asyncio
async def test_missing_farewell_or_receipt_times_out_without_claiming_goal_completed(with_audio):
    async with running_bridge(end_call_timeout=0.3) as (phone, model, bridge, task):
        await request_end(model)
        if with_audio:
            await farewell_audio(model)
        result = await asyncio.wait_for(task, 2)
        assert result["status"] == "failed"
        assert result["error"] == "end_call_playback_timeout"
        assert result["end_call"]["status"] == "playback_timeout"


@pytest.mark.asyncio
async def test_peer_disconnect_during_farewell_is_not_reported_as_auto_end():
    async with running_bridge() as (phone, model, bridge, task):
        await request_end(model)
        await phone.input.put(
            {"event": "stop", "stop": {"accountId": "ACtest", "callId": "CAtest"}}
        )
        result = await task
        assert result["end_call"]["status"] == "not_played"
