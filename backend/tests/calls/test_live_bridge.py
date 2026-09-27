import asyncio
import base64
import json

import pytest
import pytest_asyncio


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
            "barge_in_count": 0,
            "dropped_audio_bytes": 0,
            "cleared_unacked_bytes": 0,
        }
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # Normal cleanup does not pretend there was a user interruption.
    await bridge._clear_output()
    assert store.activity(owner, call)["events"][-1]["content"]["interrupted"] is False
    await bridge._clear_output(interrupted=True)
    assert store.activity(owner, call)["events"][-1]["content"]["interrupted"] is True


@pytest_asyncio.fixture
async def ending_bridge(monkeypatch):
    from types import SimpleNamespace

    from test_native_audio import Socket, until

    from agent_service.calls import live_bridge

    clock = [100.0]
    monkeypatch.setattr(live_bridge, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Model(Socket):
        async def send(self, raw):
            await super().send(raw)
            e = json.loads(raw)
            if e["type"].endswith(".append") and "event_id" in e:
                await self.input.put({"type": e["type"] + "ed", "client_event_id": e["event_id"]})

    class Media:
        def __init__(self):
            self.input = asyncio.Queue()
            self.sent = []
            self.gate = asyncio.Event()
            self.gate.set()
            self.mark_gate = asyncio.Event()
            self.mark_gate.set()

        async def events(self):
            while True:
                yield await self.input.get()

        async def send(self, event):
            if event["event"] == "media":
                await self.gate.wait()
            if event["event"] == "mark":
                await self.mark_gate.wait()
            self.sent.append(event)

    model, media = Model(), Media()
    bridge = live_bridge.LiveBridge(model, listen_first=True)
    bridge.media = media
    bridge.ready.set()
    tasks = [asyncio.create_task(f()) for f in (bridge.receive, bridge.play, bridge.phone)]
    await bridge.end_call("goal_achieved", "합성 시험 결과", spoken_result="안녕히 계세요")

    async def audio(raw):
        before = bridge.generated_bytes
        await model.input.put(
            {"type": "session.output_audio.delta", "delta": base64.b64encode(raw).decode()}
        )
        await until(lambda: bridge.generated_bytes == before + len(raw))

    async def ack():
        await until(lambda: any(e["event"] == "mark" for e in media.sent))
        marks = [e for e in media.sent if e["event"] == "mark"]
        assert marks, "The final voiced packet must be marked even below 100ms."
        await media.input.put(marks[-1])
        await until(lambda: bridge.played_bytes > 0)

    yield bridge, media, clock, audio, ack, tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_end_waits_for_final_voice_ack_but_not_continuous_silence(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 160)
    await until(lambda: bridge.sent_bytes == 160)
    clock[0] = 103
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.15)
    assert not finish.done()
    await ack()
    await audio(b"\xff" * 3200)
    await until(lambda: bridge.sent_bytes >= 320)
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "audio_drained"
    assert bridge.output.qsize() > 0
    assert bridge.played_bytes < bridge.sent_bytes
    assert bridge.report()["end_call"]["playback"]["voice_end_bytes"] == 160


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocker", ["missing_ack", "new_voice", "partial_voice", "sending_voice", "no_voice"]
)
async def test_unfinished_voice_never_becomes_playback_success(ending_bridge, blocker):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    if blocker != "no_voice":
        await audio(b"\xaa" * 160)
        await until(lambda: bridge.sent_bytes == 160)
        if blocker != "missing_ack":
            finish = asyncio.create_task(bridge.finish())
            tasks.append(finish)
            await asyncio.sleep(0.15)
            await ack()
            finish.cancel()
            await asyncio.gather(finish, return_exceptions=True)
    clock[0] = 103
    if blocker == "new_voice":
        await audio(b"\xaa" * 160)
        await until(lambda: bridge.sent_bytes == 320)
    elif blocker == "partial_voice":
        await audio(b"\xaa" * 80)
    elif blocker == "sending_voice":
        media.gate.clear()
        await audio(b"\xaa" * 160)
        await until(lambda: bridge.output.empty())
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.15)
    assert not finish.done()
    clock[0] = 126
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "playback_unconfirmed"


@pytest.mark.asyncio
async def test_caller_speech_cancels_pending_hangup(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    clock[0] = 101
    await media.input.put(
        {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}
    )
    await until(lambda: bridge.input_bytes == 160)
    assert bridge.ending is None


@pytest.mark.asyncio
async def test_clear_invalidates_end_request_and_late_playback_marks(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 800)
    await until(lambda: bridge.sent_bytes == 800)
    old = [e for e in media.sent if e["event"] == "mark"][-1]
    await bridge._clear_output(interrupted=True)
    await media.input.put(old)
    await asyncio.sleep(0.02)
    assert bridge.ending is None
    assert bridge.played_bytes == 0
    assert bridge.interrupted


@pytest.mark.asyncio
async def test_new_voice_restarts_quiet_wait_even_when_acked(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 800)
    await until(lambda: bridge.sent_bytes == 800)
    await ack()
    clock[0] = 103
    await audio(b"\xaa" * 800)
    await until(lambda: bridge.sent_bytes == 1600)
    await media.input.put([e for e in media.sent if e["event"] == "mark"][-1])
    await until(lambda: bridge.played_bytes == 1600)
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.15)
    assert not finish.done()
    clock[0] = 105.1
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "audio_drained"


@pytest.mark.asyncio
async def test_voice_queued_behind_silence_prevents_early_hangup(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 800)
    await until(lambda: bridge.sent_bytes == 800)
    await ack()
    media.gate.clear()
    await audio(b"\xff" * 1600 + b"\xaa" * 160)
    clock[0] = 103
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.15)
    assert not finish.done()
    clock[0] = 126
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "playback_unconfirmed"
    assert bridge.ending["playback"]["pending_voice_packets"] == 1


@pytest.mark.asyncio
async def test_blocked_mark_send_does_not_disable_hangup_deadline(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    media.mark_gate.clear()
    await audio(b"\xaa" * 160)
    await until(lambda: bridge.sent_bytes == 160)
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.15)
    clock[0] = 126
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "playback_unconfirmed"


@pytest.mark.asyncio
async def test_completion_review_waits_for_quiet_new_context_and_has_a_budget(monkeypatch):
    from types import SimpleNamespace

    from agent_service.calls import live_bridge

    clock = [100.0]
    monkeypatch.setattr(live_bridge, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    seen = []

    class Coordinator:
        async def review_completion(self, version):
            seen.append(version)
            return True

    b = live_bridge.LiveBridge(None)
    b.coordinator = Coordinator()
    b.transcripts = [{"role": "caller", "text": "네"}, {"role": "assistant", "text": "감사합니다"}]
    b.transcript_version = 2
    assert not await b.review_tick()  # Too early after connecting.
    clock[0] = 111
    b.last_input_sound = 110
    assert not await b.review_tick()  # Caller speaking.
    clock[0] = 114
    assert await b.review_tick()
    clock[0] = 130
    assert not await b.review_tick()  # Same transcript never repeats.
    b.transcript_version = 3
    b.pending_voice = 1
    assert not await b.review_tick()
    b.pending_voice = 0
    b.last_review_at = 125
    assert not await b.review_tick()  # Ten-second rate limit.
    b.last_review_at = 110
    assert await b.review_tick()
    b.transcript_version = 4
    b.review_count = 12
    clock[0] = 200
    assert not await b.review_tick()
    assert seen == [2, 3]


@pytest.mark.asyncio
async def test_end_cancellation_is_audited_without_transcript_or_summary(ending_bridge):
    from types import SimpleNamespace

    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    events = []

    async def db(method, *args):
        events.append(args)

    bridge.coordinator = SimpleNamespace(db=db, store=SimpleNamespace(event=None), call_id="test")
    clock[0] = 101
    await media.input.put(
        {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}
    )
    await until(lambda: bool(events))
    assert bridge.ending is None
    assert events == [("test", "end_canceled", {"reason": "caller_audio_resumed"})]


@pytest.mark.asyncio
async def test_canceled_farewell_is_reviewed_then_new_farewell_drains(ending_bridge, tmp_path):
    from test_live_store import setup_live
    from test_native_audio import until

    from agent_service.calls.delegation import DelegationCoordinator

    bridge, media, clock, audio, ack, tasks = ending_bridge
    _, calls, store, owner, call = setup_live(tmp_path)

    async def runner(context, tools):
        assert context["completion_review"]
        await tools[0].ainvoke({"reason": "goal_achieved", "summary": "합성 추가 확인 완료"})
        return "확인 감사합니다. 안녕히 계세요."

    c = DelegationCoordinator(calls, call, bridge, runner=runner)
    bridge.coordinator = c
    try:
        clock[0] = 101
        await media.input.put(
            {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}
        )
        await until(lambda: bridge.ending is None)
        bridge.transcripts = [
            {"role": "caller", "text": "네, 추가 확인도 됐어요."},
            {"role": "assistant", "text": "감사합니다."},
        ]
        bridge.transcript_version = 2
        clock[0] = 114
        assert await bridge.review_tick()
        await c.task
        assert bridge.ending["status"] == "waiting_for_playback"
        await audio(b"\xaa" * 160)
        await ack()
        clock[0] = 117
        await asyncio.wait_for(bridge.finish(), 1)
        assert bridge.report()["end_call"]["status"] == "audio_drained"
        kinds = [e["kind"] for e in store.activity(owner, call)["events"]]
        for kind in (
            "end_canceled",
            "end_tool_requested",
            "end_requested",
            "completion_review_finished",
            "end_playback_finished",
        ):
            assert kind in kinds
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_speech_during_end_diagnostic_write_still_cancels_hangup(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 160)
    await ack()
    reached, release = asyncio.Event(), asyncio.Event()

    async def audit(kind, content):
        if kind == "end_playback_finished":
            reached.set()
            await release.wait()

    bridge.audit = audit
    clock[0] = 103
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await reached.wait()
    await media.input.put(
        {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}
    )
    await until(lambda: bridge.ending is None)
    release.set()
    await asyncio.sleep(0.15)
    assert not finish.done()


@pytest.mark.asyncio
async def test_already_spoken_farewell_waits_for_existing_ack_without_new_speech(ending_bridge):
    bridge, media, clock, audio, ack, tasks = ending_bridge
    await audio(b"\xaa" * 160)
    from test_native_audio import until

    await until(lambda: bridge.sent_bytes == 160)
    bridge.ending = None
    commands = []

    async def command(kind, content, *args):
        commands.append((kind, content))

    bridge.command = command
    await bridge.end_call(
        "goal_achieved",
        "internal summary",
        spoken_result="DO_NOT_REPEAT",
        farewell_already_said=True,
    )
    assert "DO_NOT_REPEAT" not in str(commands)
    assert bridge.ending["heard_output"]
    clock[0] = 103
    finish = asyncio.create_task(bridge.finish())
    tasks.append(finish)
    await asyncio.sleep(0.02)
    assert not finish.done()
    await ack()
    await asyncio.wait_for(asyncio.shield(finish), 0.5)
    assert bridge.ending["status"] == "audio_drained"


@pytest.mark.asyncio
async def test_already_spoken_without_audio_cannot_claim_playback_success(ending_bridge):
    bridge, media, clock, audio, ack, tasks = ending_bridge
    bridge.ending = None
    await bridge.end_call("goal_achieved", "test", farewell_already_said=True)
    assert not bridge.ending["heard_output"]
    clock[0] = 126
    await asyncio.wait_for(bridge.finish(), 0.5)
    assert bridge.ending["status"] == "playback_unconfirmed"


@pytest.mark.asyncio
async def test_farewell_cancellation_resumes_model_without_blocking_input(ending_bridge):
    from test_native_audio import until

    bridge, media, clock, audio, ack, tasks = ending_bridge
    commands = []
    release = asyncio.Event()

    async def delayed_command(kind, content, delegation_id=None):
        commands.append((kind, content))
        await release.wait()

    bridge.command = delayed_command
    tasks.append(asyncio.create_task(bridge.resume_after_end()))
    clock[0] += 1
    packet = {"event": "media", "media": {"payload": base64.b64encode(b"\xaa" * 160).decode()}}
    await media.input.put(packet)
    await until(lambda: commands)
    assert bridge.ending is None
    assert any(e["event"] == "clear" for e in media.sent)
    assert commands[0][0] == "session.instructions.append"
    await media.input.put(packet)
    await until(lambda: bridge.input_bytes == 320)
    assert len(commands) == 1  # One cancellation, not a new command for every packet.
    release.set()


@pytest.mark.asyncio
async def test_old_resume_does_not_override_newer_end_decision(ending_bridge):
    bridge, media, clock, audio, ack, tasks = ending_bridge
    original_end = bridge.ending
    before = len(bridge.model.sent)
    bridge.end_resume_requested.set()  # Old cancellation waiting behind a newer decision.
    worker = asyncio.create_task(bridge.resume_after_end())
    tasks.append(worker)
    for _ in range(5):
        await asyncio.sleep(0)
    assert bridge.ending is original_end
    assert len(bridge.model.sent) == before
    assert not bridge.end_resume_requested.is_set()
