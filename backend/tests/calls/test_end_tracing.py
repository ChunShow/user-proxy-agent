import pytest
from test_call_store import spec
from test_manager import manager
from test_native_audio import until


@pytest.mark.asyncio
@pytest.mark.parametrize("already_ended", [False, True])
async def test_carrier_end_trace_distinguishes_hangup_from_prior_close(tmp_path, already_ended):
    from agent_service.calls.live_store import LiveStore

    m, g, db, s, o, c, u = manager(tmp_path)
    g.report = {
        "mode": "gpt_live",
        "end_call": {"reason": "goal_achieved", "status": "audio_drained"},
    }
    call = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    if already_ended:
        g.status = "completed"
    g.audio_gate.set()
    await until(lambda: not m.tasks)
    events = LiveStore(s).activity(o, call["id"])["events"]
    kinds = [e["kind"] for e in events]
    assert "carrier_end_confirmed" in kinds
    assert ("carrier_hangup_requested" in kinds) is not already_ended
    if not already_ended:
        assert kinds.index("carrier_hangup_requested") < kinds.index("carrier_hangup_returned")
    assert s.get(o, call["id"])["status"] == "ended"


@pytest.mark.asyncio
async def test_carrier_hangup_failure_is_recorded_without_false_confirmation(tmp_path):
    from agent_service.calls.live_store import LiveStore

    m, g, db, s, o, c, u = manager(tmp_path)
    g.hangup_error = True
    call = await m.start(o, c, u, spec())
    g.audio_gate.set()
    await until(lambda: not m.tasks)
    kinds = [e["kind"] for e in LiveStore(s).activity(o, call["id"])["events"]]
    assert "carrier_hangup_failed" in kinds
    assert "carrier_end_unconfirmed" in kinds
    assert "carrier_end_confirmed" not in kinds
    g.hangup_error = False
    await m.shutdown()


def test_call_event_export_allows_only_codes_and_numbers(monkeypatch):
    from unittest.mock import Mock

    from agent_service import observability as obs

    client = Mock()
    monkeypatch.setattr(obs, "get_trace_client", lambda: client)
    identity = {"conversation_id": "conv", "source_user_message_id": "msg", "id": "call"}
    obs.record_call_event(
        identity,
        "end_playback_finished",
        {
            "status": "audio_drained",
            "wait_ms": 123,
            "summary": "PRIVATE",
            "reason": "PRIVATE",
            "queued_packets": "PRIVATE",
            "acked_bytes": 456,
        },
    )
    obs.record_call_event(identity, "transcript", {"text": "PRIVATE"})
    import json

    client.start_observation.assert_called_once()
    data = client.start_observation.call_args.kwargs
    assert data["name"] == "call.end_playback_finished"
    assert data["metadata"]["wait_ms"] == 123
    assert data["metadata"]["acked_bytes"] == 456
    assert "PRIVATE" not in json.dumps(data)
    client.start_observation.return_value.end.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_farewell_command_ack_and_failure_are_distinct(fail):
    from agent_service.calls.live_bridge import LiveBridge

    bridge = LiveBridge(None)
    events = []

    async def audit(kind, content):
        events.append(kind)

    async def command(*args):
        if fail:
            raise TimeoutError("private provider response")

    bridge.audit = audit
    bridge.command = command
    if fail:
        with pytest.raises(TimeoutError):
            await bridge.end_call("goal_achieved", "private summary")
    else:
        await bridge.end_call("goal_achieved", "private summary")
    assert events == [
        "end_requested",
        "farewell_commands_started",
        "farewell_commands_failed" if fail else "farewell_commands_acked",
    ]


@pytest.mark.asyncio
async def test_end_tool_then_runner_cancellation_is_recorded(tmp_path):
    import asyncio

    from test_live_store import setup_live

    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, owner, call = setup_live(tmp_path)
    entered = asyncio.Event()

    class Bridge:
        transcripts = [{"role": "caller", "text": "synthetic"}]

    async def runner(context, tools):
        await next(t for t in tools if t.name == "end_call").ainvoke(
            {"reason": "goal_achieved", "summary": "synthetic"}
        )
        entered.set()
        await asyncio.Event().wait()

    c = DelegationCoordinator(calls, call, Bridge(), runner=runner)
    await c.request("test")
    await entered.wait()
    await c.close()
    kinds = [e["kind"] for e in store.activity(owner, call)["events"]]
    assert "end_tool_requested" in kinds
    assert "end_decision_canceled" in kinds
    assert "end_final_answer_ready" not in kinds


@pytest.mark.asyncio
async def test_diagnostics_failure_does_not_block_direct_hangup(tmp_path, monkeypatch):
    from agent_service.calls.live_store import LiveStore

    m, g, db, s, o, c, u = manager(tmp_path)

    def broken(*args):
        raise RuntimeError("diagnostics unavailable")

    monkeypatch.setattr(LiveStore, "event", broken)
    call = await m.start(o, c, u, spec())
    await until(lambda: g.dials == 1)
    await m.stop(o, call["id"])
    await until(lambda: not m.tasks)
    assert s.get(o, call["id"])["status"] == "ended"
    assert g.hangups == 1


def test_all_carrier_terminal_codes_are_recordable():
    from agent_service.calls.types import TERMINAL
    from agent_service.observability import _EVENT_ENUMS

    assert TERMINAL <= _EVENT_ENUMS["status"]
