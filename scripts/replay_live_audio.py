#!/usr/bin/env python3
"""Offline replay of recorded model arrivals through old pacing and current LiveBridge.

No API, carrier, database or credentials. Python 3.12. Models a receiver audio clock,
not ClawOps itself. Run this in a dedicated process because the virtual clock is global.
"""

import argparse
import asyncio
import audioop
import base64
import json
import wave
from collections import deque
from pathlib import Path
from statistics import median
from time import time

from agent_service.calls import live_bridge
from agent_service.settings import ROOT


def audio_clock_gaps(sent):
    cursor, previous, gaps = 0.0, False, []
    for stamp, _, size, voiced in sent:
        if stamp - cursor > 0.04 and previous and voiced:
            gaps.append(round((stamp - cursor) * 1000))
        cursor = max(cursor, stamp) + size / 8000
        previous = voiced
    return gaps


async def replay(frames, raw, *, current):
    clock, cursor, pending, sent = [0.0], 0, deque(), []
    output = bytearray()

    def feed():
        nonlocal cursor
        while cursor < len(frames) and frames[cursor][0] <= clock[0] + 1e-8:
            _, start, size, _ = frames[cursor]
            bridge.generated_bytes += size
            pending.extend(raw[i : i + 160] for i in range(start, start + size, 160))
            cursor += 1

    class Queue:
        async def get(self):
            feed()
            if not pending:
                if cursor == len(frames):
                    raise EOFError
                clock[0] = max(clock[0], frames[cursor][0])
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
                block = base64.b64decode(event["media"]["payload"])
                sent.append([clock[0], len(output), len(block), live_bridge.has_sound(block)])
                output.extend(block)
            else:
                bridge.marks.pop(event["mark"]["name"], None)

    old_time, old_sleep = live_bridge.time.monotonic, live_bridge.asyncio.sleep
    try:
        live_bridge.time.monotonic = lambda: clock[0]
        live_bridge.asyncio.sleep = sleep
        bridge = live_bridge.LiveBridge(None)
        bridge.output, bridge.media = Queue(), Media()
        bridge.ready.set()
        if current:
            await bridge.play()
        else:
            # Pacing before continuity fix, retaining its 100ms catch-up window.
            deadline = clock[0]
            while True:
                packet = await bridge.output.get()
                deadline = max(deadline, clock[0] - 0.1)
                await bridge.media.send(
                    {"event": "media", "media": {"payload": base64.b64encode(packet).decode()}}
                )
                deadline += len(packet) / 8000
                await sleep(max(0, deadline - clock[0]))
    except EOFError:
        pass
    finally:
        live_bridge.time.monotonic, live_bridge.asyncio.sleep = old_time, old_sleep
    if not sent:
        raise ValueError("no_audio")
    return {
        "voice_gap_ms": audio_clock_gaps(sent),
        "identical": bytes(output) == raw,
        "first_packet_ms": round(sent[0][0] * 1000),
    }, sent


def save_playout(path, raw, sent):
    timeline = bytearray()
    for stamp, offset, size, _ in sent:
        start = max(len(timeline), round(stamp * 8000))
        timeline.extend(b"\xff" * (start - len(timeline)))
        timeline.extend(raw[offset : offset + size])
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(audioop.ulaw2lin(timeline, 2))


async def main(args):
    output = ROOT / "var/korean-live-replay" / str(time()).replace(".", "-")
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for path in sorted(args.directory.glob("*/result.json")):
        record = json.loads(path.read_text())
        frames = record.get("frames")
        if not frames or record.get("errors"):
            continue
        with wave.open(str(path.parent / "model.wav")) as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (
                1,
                2,
                8000,
            ):
                raise ValueError("unsupported_wave_format")
            raw = audioop.lin2ulaw(source.readframes(source.getnframes()), 2)
        expected, stamp = 0, -1.0
        for arrival, offset, size, _ in frames:
            if arrival < stamp or offset != expected or size <= 0 or size % 160:
                raise ValueError("unsupported_or_noncontiguous_frames")
            expected += size
            stamp = arrival
        if expected != len(raw):
            raise ValueError("audio_length_mismatch")
        before, old = await replay(frames, raw, current=False)
        after, new = await replay(frames, raw, current=True)
        stamps = {offset: stamp for stamp, offset, _, _ in old}
        delta = [
            round((stamp - stamps[offset]) * 1000) for stamp, offset, _, voiced in new if voiced
        ]
        row = {
            "name": path.parent.name,
            "before": before,
            "after": after,
            "additional_voice_delay_ms": {
                "min": min(delta, default=0),
                "median": median(delta) if delta else 0,
                "max": max(delta, default=0),
            },
        }
        folder = output / path.parent.name
        folder.mkdir()
        for name, sent in [("before", old), ("after", new)]:
            save_playout(folder / f"{name}.wav", raw, sent)
            (folder / f"{name}.json").write_text(json.dumps(sent))
        rows.append(row)
        print(json.dumps(row), flush=True)
    (output / "summary.json").write_text(json.dumps(rows, indent=2))
    print("Artifacts:", output)
    if not rows or any(not r["after"]["identical"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "var/korean-live-lab")
    asyncio.run(main(parser.parse_args()))
