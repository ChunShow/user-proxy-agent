import asyncio
import base64
import json

import pytest


@pytest.mark.asyncio
async def test_live_audio_transcripts_and_ack_do_not_block_receive():
    from agent_service.calls.live_bridge import LiveBridge

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = []

        async def send(self, raw):
            e = json.loads(raw)
            self.sent.append(e)
            if e["type"] == "session.commentary.append":
                await self.events.put(
                    json.dumps(
                        {"type": "session.commentary.appended", "client_event_id": e["event_id"]}
                    )
                )

        async def recv(self):
            return await self.events.get()

    socket = Socket()
    bridge = LiveBridge(socket)
    worker = asyncio.create_task(bridge.receive())
    try:
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.input_transcript.delta",
                    "delta": "화요일 가능?",
                    "start_ms": 0,
                    "end_ms": 400,
                }
            )
        )
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.output_audio.delta",
                    "delta": base64.b64encode(b"\xaa" * 160).decode(),
                }
            )
        )
        await bridge.command("session.commentary.append", "가능합니다", "d1")
        assert bridge.transcripts[0]["text"] == "화요일 가능?"
        assert bridge.output.qsize() == 1
        assert not bridge.ending
        assert bridge.played_bytes == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_playback_uses_audio_clock_not_send_time(monkeypatch):
    from agent_service.calls import live_bridge
    from agent_service.calls.live_bridge import LiveBridge

    clock = [0.0]
    monkeypatch.setattr(live_bridge.time, "monotonic", lambda: clock[0])

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(live_bridge.asyncio, "sleep", sleep)

    class Media:
        packets = 0

        async def send(self, event):
            clock[0] += 0.007  # Control/socket work must not add to every 20ms frame.
            if event["event"] == "media":
                self.packets += 1
                if self.packets == 20:
                    raise EOFError

    bridge = LiveBridge(None)
    bridge.media = Media()
    bridge.ready.set()
    for _ in range(20):
        bridge.output.put_nowait(b"\xff" * 160)
    with pytest.raises(EOFError):
        await bridge.play()
    assert clock[0] < 0.41


@pytest.mark.asyncio
async def test_verified_result_releases_waiting_state_and_requests_speech():
    from agent_service.calls.live_bridge import LiveBridge

    bridge = LiveBridge(None)
    commands = []

    async def command(*args):
        commands.append(args)

    bridge.command = command
    await bridge.deliver_result("오후 4시에는 가능합니다.", "d1")
    assert [c[0] for c in commands] == [
        "session.thinking.append",
        "session.commentary.append",
        "session.instructions.append",
    ]
    assert "대기" in commands[0][1]
    assert "4시" in commands[1][1]
    assert "지금" in commands[2][1]


def test_backend_final_reply_excludes_progress_before_tools():
    from langchain_core.messages import AIMessage, HumanMessage

    from agent_service.calls.delegation import final_reply

    assert (
        final_reply(
            [
                HumanMessage(content="문의"),
                AIMessage(content="확인하겠습니다"),
                AIMessage(content="오후 4시 가능합니다"),
            ]
        )
        == "오후 4시 가능합니다"
    )


@pytest.mark.asyncio
async def test_progress_separates_generated_sent_and_acked_audio_and_records_stop(tmp_path):
    from types import SimpleNamespace

    from test_live_store import setup_live
    from test_native_audio import Socket, phone_start, until

    from agent_service.calls.live_bridge import LiveBridge
    from agent_service.calls.media import MediaProtocol, NativeMedia

    _, _, store, owner, call = setup_live(tmp_path)

    async def db(fn, *args):
        return fn(*args)

    async def close():
        pass

    model, phone = Socket(), Socket()
    bridge = LiveBridge(model, listen_first=True)
    bridge.coordinator = SimpleNamespace(store=store, call_id=call, db=db, close=close)
    media = NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))
    task = asyncio.create_task(bridge.run(media))
    try:
        await phone.input.put(phone_start())
        await model.input.put(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(b"\xff" * 1600).decode(),
            }
        )
        await until(lambda: bridge.sent_bytes == 1600)
        # The carrier has not acknowledged playback just because audio was sent.
        await phone.input.put({"event": "mark", "mark": {"name": "800"}})
        await until(lambda: bridge.played_bytes == 800)
        await asyncio.sleep(1.05)
        progress = [
            e for e in store.activity(owner, call)["events"] if e["kind"] == "audio_progress"
        ]
        assert len(progress) == 1
        assert progress[0]["content"] == {
            "generated_bytes": 1600,
            "sent_bytes": 1600,
            "playback_acked_bytes": 800,
            "interrupted": False,
        }
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # Normal cleanup does not pretend there was a user interruption.
    await bridge._clear_output()
    assert store.activity(owner, call)["events"][-1]["content"]["interrupted"] is False
    await bridge._clear_output(interrupted=True)
    assert store.activity(owner, call)["events"][-1]["content"]["interrupted"] is True
