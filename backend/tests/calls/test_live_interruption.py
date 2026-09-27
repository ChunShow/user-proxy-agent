import asyncio
import base64
import json

import pytest

from agent_service.calls.live_bridge import LiveBridge


@pytest.mark.asyncio
async def test_caller_speech_clears_carrier_and_local_audio_without_waiting_for_model_ack():
    from agent_service.calls.interruption import Interruption

    class Socket:
        async def send(self, raw):
            pass  # Deliberately never ACK.

    class Media:
        sent = []

        async def send(self, event):
            self.sent.append(event)

    bridge = LiveBridge(Socket())
    bridge.media = Media()
    bridge.ready.set()
    bridge.output.put_nowait(b"\x00" * 160)
    bridge.pending_voice = 1
    bridge.marks["old"] = 160
    bridge.ending = {"status": "waiting_for_playback"}
    control = Interruption(bridge)
    control.detector.speech_ms = 400
    control.detector.last_speech = 1.0
    await asyncio.wait_for(control.check_input(1.0), 0.2)
    assert control.blocked
    assert bridge.output.empty() and bridge.pending_voice == 0
    assert bridge.media.sent == [{"event": "clear"}]
    assert not bridge.marks and bridge.ending is None
    # Late old output must not be sent after clear.
    assert not control.accept_output(b"\x00" * 160, 1.1)
    await control.close()


@pytest.mark.asyncio
async def test_ack_alone_or_input_pause_does_not_release_old_speech():
    from agent_service.calls.interruption import Interruption

    class Bridge:
        pending_voice = 1
        last_output_sound = 1.0

        async def _clear_output(self, **kwargs):
            pass

        async def command(self, *args):
            pass

        async def audit(self, *args):
            pass

    control = Interruption(Bridge())
    control.detector.speech_ms = 400
    control.detector.last_speech = 1.0
    await control.check_input(1.0)
    await asyncio.sleep(0)
    for i in range(50):
        assert not control.accept_output(b"\x00" * 160, 1.0 + i * 0.02)
    await control.tick(2.0)
    assert control.blocked  # ACK + user quiet cannot release ongoing old speech.
    for i in range(20):
        assert not control.accept_output(b"\xff" * 160, 2.0 + i * 0.02)
    await control.tick(2.4)
    assert not control.blocked
    assert control.accept_output(b"\x00" * 160, 2.42)
    assert control.count == 1
    await control.close()


@pytest.mark.asyncio
async def test_bridge_filters_late_audio_and_keeps_receiving_transcript_when_blocked():
    from agent_service.calls.interruption import Interruption

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()

        async def recv(self):
            return await self.events.get()

    socket = Socket()
    bridge = LiveBridge(socket)
    bridge.interruption = Interruption(bridge)
    bridge.interruption.blocked = True
    worker = asyncio.create_task(bridge.receive())
    try:
        for event in [
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(b"\x00" * 320).decode(),
            },
            {"type": "session.input_transcript.delta", "delta": "오후 여섯 시요"},
            {"type": "session.closed"},
        ]:
            await socket.events.put(json.dumps(event))
        await worker
        assert bridge.output.empty()
        assert bridge.pending_voice == 0
        assert bridge.transcripts[-1]["text"] == "오후 여섯 시요"
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def test_speech_detection_rejects_brief_sound_and_correlated_playback():
    from agent_service.calls.interruption import SpeechDetector

    class Voice:
        def is_speech(self, frame, rate):
            return True

    # Independent boundary classifier; test persistence and echo rejection, not WebRTC internals.
    gate = SpeechDetector()
    gate.vad = Voice()
    packet = bytes(range(160))
    for i in range(10):
        gate.feed(packet, i * 0.02)
    assert gate.speech_ms < 320
    gate.feed(b"\xff" * 160, 0.4)
    assert gate.speech_ms == 0
    for i in range(20):
        gate.played(packet, 1 + i * 0.02)
        gate.feed(packet, 1.08 + i * 0.02)
    assert gate.speech_ms == 0
    for i in range(20):
        gate.feed(packet, 3 + i * 0.02)
    assert gate.speech_ms >= 320


@pytest.mark.asyncio
async def test_stalled_output_and_failed_command_cannot_resume_playback():
    from agent_service.calls.interruption import Interruption
    from agent_service.calls.types import ProviderFailure

    control = Interruption(None)
    control.blocked = True
    control.began = 1
    control.detector.last_speech = 1
    control.quiet_bytes = 3200
    control.last_model_packet = 1
    control.command_task = asyncio.create_task(asyncio.sleep(0))
    await control.command_task
    await control.tick(3)
    assert control.blocked
    with pytest.raises(ProviderFailure, match="recovery_timeout"):
        await control.tick(14)

    async def fail():
        raise TimeoutError

    control.command_task = asyncio.create_task(fail())
    await asyncio.gather(control.command_task, return_exceptions=True)
    with pytest.raises(ProviderFailure, match="command_failed"):
        await control.tick(3)
    assert control.blocked


@pytest.mark.asyncio
async def test_cleared_unacked_audio_is_not_counted_as_heard_by_later_ack():
    class Media:
        async def send(self, event):
            pass

    bridge = LiveBridge(None)
    bridge.media = Media()
    bridge.sent_bytes, bridge.played_bytes = 1600, 800
    await bridge._clear_output(interrupted=True)
    bridge.sent_bytes = bridge.played_bytes = 2400
    assert bridge.report()["played_audio_ms"] == 200


@pytest.mark.asyncio
async def test_assistant_backchannel_during_existing_caller_turn_is_not_new_barge_in():
    from types import SimpleNamespace

    from agent_service.calls.interruption import Interruption

    control = Interruption(SimpleNamespace(pending_voice=1, last_output_sound=1.0))
    control.detector.last_speech = 1.0
    control.detector.speech_ms = 1000
    control.accept_output(b"\x00" * 160, 1.0)
    await control.check_input(1.01)
    assert not control.blocked


def test_actual_vad_rejects_silence_and_low_level_random_noise():
    import random

    from agent_service.calls.interruption import SpeechDetector

    gate = SpeechDetector()
    randomizer = random.Random(22)
    peak = 0
    for i in range(100):
        raw = bytes(
            randomizer.choice([210, 215, 220, 225, 230, 235, 240, 245, 250, 255])
            for _ in range(160)
        )
        gate.feed(raw, i * 0.02)
        peak = max(peak, gate.speech_ms)
    assert peak < 320
    gate.feed(b"\xff" * 8000, 3.0)
    assert gate.speech_ms == 0


@pytest.mark.asyncio
async def test_interruption_during_result_ack_prevents_old_commentary():
    bridge = LiveBridge(None)
    commands = []

    async def command(kind, *args):
        commands.append(kind)
        bridge.interruption.count += 1

    bridge.command = command
    await bridge.deliver_result("오래된 결과", "old")
    assert commands == ["session.thinking.append"]
