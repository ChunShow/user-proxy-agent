#!/usr/bin/env python3
"""Opt-in Korean audio experiment, Python 3.12/macOS. Never opens a carrier or Google client.

--prepare creates synthetic Yuna WAV fixtures locally. --run makes ONE billable Live session.
Outputs are ignored local artifacts; objective measurements do not rate perceived naturalness.
"""

import argparse
import asyncio
import audioop
import base64
import json
import re
import subprocess
import time
import wave
from contextlib import suppress
from pathlib import Path

from live_korean_metrics import duration, summarize

from agent_service.calls.live import INSTRUCTIONS, live_url
from agent_service.calls.live_bridge import LiveBridge, has_sound
from agent_service.calls.realtime import NoRedirectConnect
from agent_service.calls.settings import CallSettings
from agent_service.settings import ROOT

LAB = ROOT / "var/korean-live-lab"
FIXTURES = Path(__file__).parent / "fixtures/live-korean"
TEXTS = {
    "question": "안녕하세요. 구월 이십팔일 오전 열 시 십 분에 만나는 건데요. "
    "날짜와 시간을 다시 말해 주시고, 만나기 전에 준비할 것도 두 가지만 알려 주세요.",
    "correction": "잠깐만요. 제 말 먼저 들어 주세요. 열 시가 아니라 오후 여섯 시예요. "
    "날짜는 그대로고요. 바뀐 시간만 짧게 다시 말해 주세요.",
    "backchannel": "네.",
}
TASK = (
    "실제 일정 등록이나 조회 없는 음성 대화 연습입니다. 상대가 말한 날짜와 시간을 "
    "읽어 주고, 준비물로 메모장과 펜을 안내하세요. 확인된 정보의 단순 반복은 직접 "
    "답하세요. 통화를 끝내지는 마세요."
)


def prepare():
    LAB.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, text in TEXTS.items():
        subprocess.run(
            ["say", "-v", "Yuna", "-r", "175", "-o", str(LAB / f"{name}.aiff"), text],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@8000",
                "-c",
                "1",
                str(LAB / f"{name}.aiff"),
                str(LAB / f"{name}.wav"),
            ],
            check=True,
            capture_output=True,
        )


def load(name):
    with wave.open(str(LAB / f"{name}.wav")) as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 8000):
            raise ValueError("fixture_must_be_mono_pcm16_8000")
        return audioop.lin2ulaw(source.readframes(source.getnframes()), 2)


def save_audio(path, raw):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(audioop.ulaw2lin(raw, 2))


async def run(args):
    out = LAB / args.name
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    cfg = CallSettings.load()  # Reads credentials only; no carrier/network work.
    instructions = (
        INSTRUCTIONS
        if args.prompt == "current"
        else (FIXTURES / f"{args.prompt}.txt").read_text().strip()
    )
    question = load("question")
    interrupt = load(args.scenario) if args.scenario != "none" else b""
    model_audio, phone_audio, input_audio = bytearray(), bytearray(), bytearray()
    frames, sent, transcripts, markers, errors = [], [], [], [], []
    events, tasks = {}, []
    first_voice = None
    accepted_voice = None
    began = time.monotonic()

    def now():
        return time.monotonic() - began

    try:
        async with (
            asyncio.timeout(args.seconds + 30),
            NoRedirectConnect(
                live_url(cfg.realtime_base_url),
                additional_headers={"api-key": cfg.realtime_api_key},
                proxy=None,
                open_timeout=12,
                close_timeout=3,
                max_size=1048576,
                max_queue=64,
            ) as ws,
        ):
            await ws.send(
                json.dumps(
                    {
                        "type": "session.start",
                        "session": {
                            "model": "gpt-live-1",
                            "instructions": instructions + "\n요청 내용:\n" + TASK,
                            "delegation": {"type": "client"},
                            "audio": {
                                "format": {"type": "audio/pcmu", "rate": 8000},
                                "output": {"voice": args.voice},
                            },
                        },
                    },
                    ensure_ascii=False,
                )
            )
            event = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if event.get("type") != "session.started":
                raise ValueError("session_not_started")
            accepted_voice = (
                event.get("session", {}).get("audio", {}).get("output", {}).get("voice")
            )
            began = time.monotonic()
            marks = asyncio.Queue()

            class Socket:
                async def send(self, raw):
                    await ws.send(raw)

                async def recv(self):
                    nonlocal first_voice
                    raw = await ws.recv()
                    event = json.loads(raw)
                    kind = event.get("type")
                    events[kind] = events.get(kind, 0) + 1
                    if kind == "session.output_audio.delta":
                        block = base64.b64decode(event["delta"])
                        frames.append([now(), len(model_audio), len(block), has_sound(block)])
                        model_audio.extend(block)
                        if (
                            has_sound(block)
                            and first_voice is None
                            and now() > len(question) / 8000 + 1
                        ):
                            first_voice = now()
                    if kind in (
                        "session.input_transcript.delta",
                        "session.output_transcript.delta",
                    ):
                        transcripts.append(
                            {
                                "t": round(now(), 3),
                                "role": "caller" if "input_" in kind else "assistant",
                                "text": event.get("delta"),
                                "start_ms": event.get("start_ms"),
                                "end_ms": event.get("end_ms"),
                            }
                        )
                    if kind == "session.delegation.created":
                        # Explicit mock, not Sol or a real app action. Count this in the result.
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "session.context.append",
                                    "event_id": "lab-result",
                                    "delegation_id": event["delegation"]["id"],
                                    "content": "외부 조회나 변경 없이 상대가 방금 말한 내용을 "
                                    "반복해서 읽어 "
                                    "주는 연습입니다. 최신 상대 발언에 따라 짧게 직접 답하세요. "
                                    "실제 업무는 실행하지 않았습니다.",
                                },
                                ensure_ascii=False,
                            )
                        )
                    return raw

            class Media:
                async def events(self):
                    yield {"event": "start"}
                    start2 = None
                    for index in range(args.seconds * 50):
                        await asyncio.sleep(max(0, began + index * 0.02 - time.monotonic()))
                        while not marks.empty():
                            yield await marks.get()
                        if (
                            args.scenario != "none"
                            and first_voice is not None
                            and start2 is None
                            and now() > first_voice + 1.2
                            and time.monotonic() - bridge.last_output_sound < 0.04
                        ):
                            start2 = index
                            markers.append(
                                {
                                    "kind": args.scenario,
                                    "t": now(),
                                    "input_index": index * 160,
                                    "clip_duration_ms": len(interrupt) / 8,
                                }
                            )
                        offset = (index - 50) * 160
                        block = (
                            question[offset : offset + 160]
                            if 0 <= offset < len(question)
                            else b"\xff" * 160
                        )
                        if start2 is not None:
                            offset = (index - start2) * 160
                            if offset < len(interrupt):
                                block = interrupt[offset : offset + 160]
                        block = block.ljust(160, b"\xff")
                        input_audio.extend(block)
                        yield {
                            "event": "media",
                            "media": {"payload": base64.b64encode(block).decode()},
                        }
                    yield {"event": "stop"}

                async def send(self, event):
                    if event["event"] == "media":
                        block = base64.b64decode(event["media"]["payload"])
                        sent.append([now(), len(phone_audio), len(block), has_sound(block)])
                        phone_audio.extend(block)
                    elif event["event"] == "mark":
                        await marks.put(event)

            bridge = LiveBridge(Socket(), listen_first=True)
            bridge.media = Media()
            bridge.ready.set()
            tasks = [
                asyncio.create_task(bridge.receive()),
                asyncio.create_task(bridge.play()),
                asyncio.create_task(bridge.phone()),
            ]
            try:
                await ws.send(
                    json.dumps(
                        {
                            "type": "session.instructions.append",
                            "event_id": "lab-start",
                            "delegation_id": None,
                            "content": "시작 안내 완료. 먼저 말하지 말고 한국어 상대 음성을 "
                            "듣고 자연스럽게 답하세요.",
                        },
                        ensure_ascii=False,
                    )
                )
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if tasks[2] not in done:
                    raise ValueError("audio_stream_ended_early")
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await ws.send(json.dumps({"type": "session.close"}))
    except Exception as error:
        errors.append(type(error).__name__)  # Never log credential-bearing exception strings.
    if not model_audio:
        errors.append("no_model_audio")
    if args.scenario != "none" and not markers:
        errors.append("interruption_not_injected")
    result = {
        "name": args.name,
        "prompt": args.prompt,
        "voice": args.voice,
        "accepted_voice": accepted_voice,
        "events": events,
        "markers": markers,
        "transcripts": transcripts,
        "errors": errors,
        **summarize(model_audio, phone_audio, frames, sent, markers),
        "frames": frames,
        "sent": sent,
        "limitations": [
            "synthetic_Yuna_input",
            "mock_carrier_ACK",
            "mock_delegation_if_requested",
            "energy_silence_is_not_semantic_yield",
            "no_perceptual_rating",
        ],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (out / "instructions.txt").write_text(instructions + "\n" + TASK)
    for name, raw in [("model", model_audio), ("playback", phone_audio), ("input", input_audio)]:
        save_audio(out / f"{name}.wav", bytes(raw))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("frames", "sent", "transcripts")},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return not errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--prompt", choices=["baseline", "candidate", "current"], default="current")
    parser.add_argument("--voice", choices=["marin", "gleam", "meridian"], default="marin")
    parser.add_argument(
        "--scenario", choices=["correction", "backchannel", "none"], default="correction"
    )
    parser.add_argument("--seconds", type=duration, default=42)
    args = parser.parse_args()
    if args.prepare:
        prepare()
        return
    if not args.name or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", args.name):
        parser.error("--run requires a unique --name (lowercase letters, numbers, _ or -)")
    if not asyncio.run(run(args)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
