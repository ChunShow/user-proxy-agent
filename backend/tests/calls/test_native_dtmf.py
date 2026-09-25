import asyncio
import json

import pytest
from test_native_audio import delta, until
from test_native_end_call import running_bridge, tool_done


def dtmf_done(
    digits="4", *, call="key1", response="r1", evidence="기타 안내는 4번", status="completed"
):
    e = tool_done(response=response, call=call, status=status)
    e["response"]["output"][0].update(
        {"name": "send_dtmf", "arguments": json.dumps({"digits": digits, "evidence": evidence})}
    )
    return e


async def menu(model, response="r1"):
    await model.input.put({"type": "input_audio_buffer.speech_started"})
    await model.input.put({"type": "input_audio_buffer.speech_stopped"})
    await model.input.put({"type": "response.created", "response": {"id": response}})


def outputs(model):
    return [
        json.loads(e["item"]["output"])
        for e in model.sent
        if e["type"] == "conversation.item.create"
    ]


@pytest.mark.asyncio
async def test_heard_menu_sends_wire_digit_once_then_listens_until_new_menu():
    async with running_bridge(listen_first=True) as (phone, model, bridge, task):
        await until(lambda: bridge.ready.is_set())
        assert model.sent == []  # No greeting for an ARS call.
        await menu(model)
        await model.input.put(dtmf_done())
        await until(lambda: outputs(model))
        assert [e for e in phone.sent if e["event"] == "dtmf"] == [
            {"event": "dtmf", "dtmf": {"digit": "4"}}
        ]
        assert outputs(model)[0]["status"] == "sent"
        assert outputs(model)[0]["next_action"] == "listen"
        assert not any(e["type"] == "response.create" for e in model.sent)
        await model.input.put(dtmf_done())  # Same call_id delivered twice.
        await model.input.put(dtmf_done("2", call="key2"))  # New ID, same menu.
        await until(lambda: len(outputs(model)) == 2)
        assert outputs(model)[-1]["error"] == "wait_for_new_prompt"
        assert len([e for e in phone.sent if e["event"] == "dtmf"]) == 1
        await menu(model, "r2")
        await model.input.put(
            dtmf_done("2", call="key3", response="r2", evidence="위치 안내는 2번")
        )
        await until(lambda: len([e for e in phone.sent if e["event"] == "dtmf"]) == 2)
        assert [e["dtmf"]["digit"] for e in phone.sent if e["event"] == "dtmf"] == ["4", "2"]
        assert [e["status"] for e in bridge.report()["dtmf_actions"]] == ["sent", "sent"]
        assert not task.done()


@pytest.mark.parametrize(
    "digits,evidence", [("44", "메뉴"), ("A", "메뉴"), (4, "메뉴"), ("#", ""), ("4", " ")]
)
@pytest.mark.asyncio
async def test_invalid_keys_or_missing_evidence_never_touch_carrier(digits, evidence):
    async with running_bridge() as (phone, model, bridge, task):
        await menu(model)
        await model.input.put(dtmf_done(digits, evidence=evidence))
        await until(lambda: outputs(model))
        assert outputs(model)[0]["error"] == "invalid_dtmf_arguments"
        assert not any(e["event"] == "dtmf" for e in phone.sent)
        assert not task.done()


@pytest.mark.parametrize("digits", ["0", "9", "*", "#"])
@pytest.mark.asyncio
async def test_supported_keypad_symbols(digits):
    async with running_bridge() as (phone, model, bridge, task):
        await menu(model)
        await model.input.put(dtmf_done(digits))
        await until(lambda: outputs(model))
        assert {"event": "dtmf", "dtmf": {"digit": digits}} in phone.sent


@pytest.mark.parametrize("speaking", [False, True])
@pytest.mark.asyncio
async def test_no_dtmf_before_a_completed_inbound_speech_turn(speaking):
    async with running_bridge() as (phone, model, bridge, task):
        if speaking:
            await model.input.put({"type": "input_audio_buffer.speech_started"})
        await model.input.put({"type": "response.created", "response": {"id": "r1"}})
        await model.input.put(dtmf_done())
        await until(lambda: outputs(model))
        assert outputs(model)[0]["error"] == "wait_for_new_prompt"
        assert not any(e["event"] == "dtmf" for e in phone.sent)


@pytest.mark.asyncio
async def test_cancelled_or_stale_response_cannot_press_key():
    async with running_bridge() as (phone, model, bridge, task):
        await menu(model)
        await model.input.put(dtmf_done(status="cancelled"))
        await model.input.put({"type": "input_audio_buffer.speech_started"})
        await model.input.put(dtmf_done(call="late"))
        await until(lambda: len(bridge.model_responses) == 2)
        assert not any(e["event"] == "dtmf" for e in phone.sent)


@pytest.mark.asyncio
async def test_model_audio_is_cleared_before_dtmf_and_not_played_after_key():
    async with running_bridge() as (phone, model, bridge, task):
        await menu(model)
        await model.input.put(delta(data=b"\xff" * 8000))
        await until(lambda: any(e["event"] == "media" for e in phone.sent))
        await model.input.put(dtmf_done())
        await until(lambda: any(e["event"] == "dtmf" for e in phone.sent))
        events = [e["event"] for e in phone.sent]
        assert events[events.index("dtmf") - 1] == "clear"
        count = len([e for e in phone.sent if e["event"] == "media"])
        await asyncio.sleep(0.06)
        assert len([e for e in phone.sent if e["event"] == "media"]) == count


@pytest.mark.asyncio
async def test_multiple_actions_in_one_response_are_rejected_before_any_side_effect():
    async with running_bridge() as (phone, model, bridge, task):
        await menu(model)
        e = dtmf_done()
        e["response"]["output"] += tool_done(call="end2")["response"]["output"]
        await model.input.put(e)
        await until(lambda: len(outputs(model)) == 2)
        assert all(x["error"] == "one_action_per_response" for x in outputs(model))
        assert not any(e["event"] == "dtmf" for e in phone.sent)
        assert bridge.report()["end_call"] is None


@pytest.mark.asyncio
async def test_send_failure_records_unknown_delivery_and_never_retries():
    async with running_bridge() as (phone, model, bridge, task):
        original = phone.send
        attempts = []

        async def fail_dtmf(message):
            if json.loads(message)["event"] == "dtmf":
                attempts.append(message)
                raise OSError("private transport details")
            await original(message)

        phone.send = fail_dtmf
        await menu(model)
        await model.input.put(dtmf_done())
        result = await asyncio.wait_for(task, 2)
        assert result["error"] == "clawops_dtmf_delivery_unknown"
        assert result["dtmf_actions"][0]["status"] == "delivery_unknown"
        assert len(attempts) == 1
        assert "private" not in json.dumps(result)
