"""Deterministic arrival jitter, without sockets, model calls or wall-clock sleeps."""

import base64
from collections import deque

import pytest

from agent_service.calls import live_bridge


async def render(monkeypatch, arrivals):
    clock, cursor, pending, sent = [0.0], 0, deque(), []

    def feed():
        nonlocal cursor
        while cursor < len(arrivals) and arrivals[cursor][0] <= clock[0] + 1e-8:
            pending.extend(arrivals[cursor][1])
            bridge.generated_bytes += sum(len(packet) for packet in arrivals[cursor][1])
            cursor += 1

    class Queue:
        async def get(self):
            feed()
            if not pending:
                if cursor == len(arrivals):
                    raise EOFError
                clock[0] = max(clock[0], arrivals[cursor][0])
                feed()
            return pending.popleft()

        def qsize(self):
            feed()
            return len(pending)

        def empty(self):
            return self.qsize() == 0

    async def sleep(delay):
        clock[0] += max(0, delay)
        feed()

    class Media:
        async def send(self, event):
            if event["event"] == "media":
                sent.append((clock[0], base64.b64decode(event["media"]["payload"])))
            else:
                bridge.marks.pop(event["mark"]["name"], None)

    monkeypatch.setattr(live_bridge.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live_bridge.asyncio, "sleep", sleep)
    bridge = live_bridge.LiveBridge(None)
    bridge.output, bridge.media = Queue(), Media()
    bridge.ready.set()
    with pytest.raises(EOFError):
        await bridge.play()
    return sent


@pytest.mark.asyncio
async def test_silent_preroll_reserves_audio_for_short_late_voice_chunk(monkeypatch):
    quiet, voice = b"\xff" * 160, b"\x00" * 160
    arrivals = [(i * 0.1 + (i // 10) * 0.06, [quiet if i < 30 else voice] * 5) for i in range(50)]
    arrivals[34] = (arrivals[34][0] + 0.09, arrivals[34][1])
    sent = await render(monkeypatch, arrivals)
    gaps, cursor, previous = [], 0.0, None
    for stamp, block in sent:
        if previous == block == voice:
            gaps.append(max(0, stamp - cursor))
        cursor = max(cursor, stamp) + len(block) / 8000
        previous = block
    assert max(gaps) < 0.04
    assert b"".join(block for _, block in sent) == b"".join(
        block for _, packets in arrivals for block in packets
    )
    assert sent[-1][0] - arrivals[-1][0] < 0.35


@pytest.mark.asyncio
async def test_short_final_packet_never_waits_indefinitely(monkeypatch):
    packet = b"\x00" * 160
    sent = await render(monkeypatch, [(0.0, [packet])])
    assert len(sent) == 1 and sent[0][1] == packet
    assert sent[0][0] <= 0.15


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["clear", "cancel"])
async def test_reserve_yields_to_clear_and_direct_cancellation(monkeypatch, action):
    import asyncio

    entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    async def sleep(_):
        entered.set()
        await release.wait()

    class Media:
        async def send(self, event):
            events.append(event)
            if event["event"] == "media":
                delivered.set()

    bridge = live_bridge.LiveBridge(None)
    bridge.media = Media()
    bridge.ready.set()
    bridge.output.put_nowait(b"\x00" * 160)
    bridge.pending_voice = 1
    bridge.generated_bytes = 160
    monkeypatch.setattr(live_bridge.asyncio, "sleep", sleep)
    task = asyncio.create_task(bridge.play())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not bridge.phone_lock.locked()
        if action == "clear":
            await asyncio.wait_for(bridge._clear_output(), 1)
            release.set()
            # A new packet must not revive the packet held before clear.
            bridge.output.put_nowait(b"\xff" * 160)
            await asyncio.wait_for(delivered.wait(), 1)
        else:
            task.cancel()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    audio = [e for e in events if e["event"] == "media"]
    if action == "clear":
        assert len(audio) == 1
        assert base64.b64decode(audio[0]["media"]["payload"]) == b"\xff" * 160
        assert bridge.pending_voice == 0 and bridge.output_epoch == 1
    else:
        assert not audio


@pytest.mark.asyncio
async def test_prebuffered_stream_has_no_added_wait_or_packet_loss(monkeypatch):
    packets = [b"\x00" * 160] * 50
    sent = await render(monkeypatch, [(0.0, packets)])
    assert sent[0][0] == 0
    assert sent[-1][0] == pytest.approx(0.98)
    assert [block for _, block in sent] == packets
