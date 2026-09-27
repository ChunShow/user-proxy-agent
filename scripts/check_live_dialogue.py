"""Opt-in multi-turn Korean audio eval. --run costs Live + main-model API usage.

No carrier client, Gmail, or Calendar. Uses production bridge/coordinator with
isolated SQLite and simulated 20ms media/ACK. macOS Yuna supplies input audio.
Recordings and transcripts stay in ignored var/live-dialogue; never print keys.
"""

import argparse
import asyncio
import audioop
import base64
import hashlib
import json
import re
import subprocess
import time
import wave
from pathlib import Path
from uuid import uuid4

from agent_service.calls.delegation import DelegationCoordinator, run_delegation
from agent_service.calls.live import LiveAudioSession
from agent_service.calls.live_bridge import LiveBridge, has_sound
from agent_service.calls.realtime import NoRedirectConnect
from agent_service.calls.settings import CallSettings
from agent_service.calls.store import CallSpec, CallStore
from agent_service.chat.schemas import ChatRequest
from agent_service.settings import ROOT, load_settings
from agent_service.storage import ConversationStore

from live_dialogue_cases import CASES, coverage, join_transcripts

LAB = ROOT / "var/live-dialogue"


def prepare():
    target = LAB / "inputs"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, case in CASES.items():
        for index, text in enumerate(case["turns"]):
            stem = target / f"{name}-{index}"
            subprocess.run(
                [
                    "say",
                    "-v",
                    "Yuna",
                    "-r",
                    "175",
                    "-o",
                    str(stem.with_suffix(".aiff")),
                    text,
                ],
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
                    str(stem.with_suffix(".aiff")),
                    str(stem.with_suffix(".wav")),
                ],
                check=True,
                capture_output=True,
            )
    print("Synthetic audio prepared", flush=True)


def input_audio(name, index):
    with wave.open(str(LAB / "inputs" / f"{name}-{index}.wav")) as source:
        assert (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
        ) == (1, 2, 8000)
        return audioop.lin2ulaw(source.readframes(source.getnframes()), 2)


def save_wav(path, raw):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(audioop.ulaw2lin(bytes(raw), 2))


async def run_case(name, repetition, args, instructions):
    case = CASES[name]
    out = LAB / args.label / f"{name}-{repetition}"
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    clips = [input_audio(name, i) for i in range(len(case["turns"]))]
    cfg = CallSettings.load()
    db = ConversationStore(out / "evaluation.sqlite3")
    db.initialize()
    owner = db.owner_for_token(db.issue_session())
    cid = str(uuid4())
    db.create_conversation(owner, cid)
    run = db.begin_run(
        owner,
        ChatRequest(request_id=uuid4(), conversation_id=cid, content=case["purpose"]),
    )
    db.save_run(owner, run, "", "completed")
    calls = CallStore(db)
    spec = CallSpec(
        destination="01000000001",
        subject="문의",
        purpose=case["purpose"],
        opening_message=case["opening"],
        questions=[case["question"]],
    )
    call = calls.register(owner, cid, run["user_message_id"], spec)
    calls.update(call["id"], status="connected")
    started = time.monotonic()
    events, turns, backend, errors = [], [], [], []
    model_audio, played_audio, incoming = bytearray(), bytearray(), bytearray()
    current_turn = -1
    last_text = last_played_voice = 0.0
    report = {}

    def now():
        return time.monotonic() - started

    class Connection:
        def __init__(self, *a, **kw):
            self.context = NoRedirectConnect(*a, **kw)

        async def __aenter__(self):
            self.ws = await self.context.__aenter__()
            return self

        async def __aexit__(self, *a):
            return await self.context.__aexit__(*a)

        async def send(self, raw):
            event = json.loads(raw)
            if event["type"] == "session.start":
                event["session"]["instructions"] = (
                    instructions
                    + "\n요청 내용:\n"
                    + json.dumps(
                        spec.model_dump(exclude={"destination"}), ensure_ascii=False
                    )
                )
            elif event["type"] != "session.input_audio.append":
                events.append(
                    {"direction": "sent", "t": now(), "turn": current_turn, **event}
                )
            await self.ws.send(json.dumps(event, ensure_ascii=False))

        async def recv(self):
            nonlocal last_text
            raw = await self.ws.recv()
            event = json.loads(raw)
            if event["type"] == "session.output_audio.delta":
                model_audio.extend(base64.b64decode(event["delta"]))
            else:
                events.append(
                    {"direction": "received", "t": now(), "turn": current_turn, **event}
                )
                if (
                    event["type"] == "session.output_transcript.delta"
                    and event.get("delta", "").strip()
                ):
                    last_text = now()
            return raw

    async def runner(context, tools):
        record = {"t": now(), "context": context}
        backend.append(record)
        try:
            result = await run_delegation(context, tools)
            record["result"] = result
            return result
        except Exception as error:
            record["error"] = type(error).__name__
            raise

    class Media:
        def __init__(self):
            self.marks = asyncio.Queue()

        async def events(self):
            nonlocal current_turn
            yield {"event": "start"}
            offset, end_input = 0, 0.0
            for frame in range(70 * 50):
                await asyncio.sleep(max(0, started + frame * 0.02 - time.monotonic()))
                while not self.marks.empty():
                    yield await self.marks.get()
                speaking = current_turn >= 0 and offset < len(clips[current_turn])
                ready = (
                    last_text > end_input
                    and now() - max(last_text, last_played_voice) > 0.65
                    and bridge.pending_voice == 0
                )
                if case.get("followup_on_farewell") and current_turn == 0:
                    ready = (
                        bool(bridge.ending)
                        and time.monotonic() - bridge.ending["requested_at"] > 0.8
                    )
                expired = now() - end_input > 20
                if (
                    not speaking
                    and current_turn + 1 < len(clips)
                    and (ready or expired)
                ):
                    current_turn += 1
                    offset = 0
                    turns.append(
                        {
                            "index": current_turn,
                            "started_at": now(),
                            "finished": False,
                            "forced_after_timeout": not ready,
                        }
                    )
                    speaking = True
                packet = b"\xff" * 160
                if speaking:
                    packet = clips[current_turn][offset : offset + 160].ljust(
                        160, b"\xff"
                    )
                    offset += 160
                    if offset >= len(clips[current_turn]):
                        end_input = now()
                        turns[-1].update(finished=True, finished_at=end_input)
                incoming.extend(packet)
                yield {
                    "event": "media",
                    "media": {"payload": base64.b64encode(packet).decode()},
                }
                if (
                    current_turn == len(clips) - 1
                    and not speaking
                    and now() - end_input > 23
                ):
                    break
            yield {"event": "stop"}

        async def send(self, event):
            nonlocal last_played_voice
            if event["event"] == "media":
                packet = base64.b64decode(event["media"]["payload"])
                start = max(len(played_audio), round(now() * 8000))
                played_audio.extend(b"\xff" * (start - len(played_audio)))
                played_audio.extend(packet)
                if has_sound(packet):
                    last_played_voice = now()
            elif event["event"] == "mark":
                await self.marks.put(event)
            else:
                events.append(
                    {
                        "direction": "mock_media",
                        "t": now(),
                        "turn": current_turn,
                        **event,
                    }
                )

    try:
        async with asyncio.timeout(90):
            async with LiveAudioSession(
                cfg.realtime_base_url,
                cfg.realtime_api_key,
                voice="marin",
                connector=Connection,
            ) as model:
                started = time.monotonic()
                bridge = LiveBridge(model, opening_message=case["opening"])
                bridge.coordinator = DelegationCoordinator(
                    calls, call["id"], bridge, runner=runner
                )
                report = await bridge.run(Media())
                if report.get("error"):
                    errors.append(report["error"])
    except Exception as error:  # noqa: BLE001 — record type only; never leak credentials
        errors.append(type(error).__name__)
    with db.connection() as sql:
        activity = [
            {"kind": r["kind"], "content": json.loads(r["content"])}
            for r in sql.execute(
                "SELECT kind,content FROM call_activity WHERE call_id=? ORDER BY id",
                (call["id"],),
            )
            if r["kind"] != "transcript"
        ]
    rows = join_transcripts(events)
    result = {
        "case": name,
        "repeat": repetition,
        "model": "gpt-live-1",
        "voice": "marin",
        "backend_model": load_settings().model_name,
        "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        "turns": turns,
        "transcripts": rows,
        "errors": errors,
        "coverage": coverage(name, turns, rows, errors),
        "report": report,
        "activity": activity,
        "backend": backend,
        "events": events,
        "review": case["review"],
        "limitations": [
            "synthetic_Yuna_input",
            "mock_carrier_and_instant_ACK",
            "no_perceptual_rating",
            "transcript_attribution_uses_input_injection_time",
        ],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (out / "instructions.txt").write_text(instructions)
    for file, audio in [
        ("input", incoming),
        ("model", model_audio),
        ("playback", played_audio),
    ]:
        save_wav(out / f"{file}.wav", audio)
    length = max(len(incoming), len(played_audio))
    mixed = audioop.add(
        audioop.mul(
            audioop.ulaw2lin(bytes(incoming).ljust(length, b"\xff"), 2), 2, 0.5
        ),
        audioop.mul(
            audioop.ulaw2lin(bytes(played_audio).ljust(length, b"\xff"), 2), 2, 0.5
        ),
        2,
    )
    save_wav(out / "conversation.wav", audioop.lin2ulaw(mixed, 2))
    print(
        json.dumps(
            {
                "case": name,
                "repeat": repetition,
                "coverage": result["coverage"],
                "errors": errors,
                "assistant": [r for r in rows if r["role"] == "assistant"],
                "end": report.get("end_call", {}),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


async def run(args):
    instructions = args.prompt.read_text().strip()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(name, repetition):
        async with semaphore:
            return await run_case(name, repetition, args, instructions)

    results = await asyncio.gather(
        *(
            one(name, repetition)
            for repetition in range(1, args.repeat + 1)
            for name in args.cases
        )
    )
    (LAB / args.label / "summary.json").write_text(
        json.dumps(
            [
                {
                    k: r[k]
                    for k in (
                        "case",
                        "repeat",
                        "coverage",
                        "errors",
                        "transcripts",
                        "report",
                    )
                }
                for r in results
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return all(
        all(
            r["coverage"][key]
            for key in (
                "all_inputs_sent",
                "all_inputs_transcribed",
                "all_turns_answered",
                "transport_ok",
                "unforced_inputs",
            )
        )
        for r in results
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--label")
    parser.add_argument("--prompt", type=Path)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=CASES,
        default=[name for name in CASES if name != "farewell_followup"],
    )
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if args.prepare:
        prepare()
        return
    if (
        not args.label
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", args.label)
        or not args.prompt
    ):
        parser.error("--run requires a unique --label and --prompt file")
    if (LAB / args.label).exists():
        parser.error("label already exists; choose a new label")
    if not args.prompt.is_file() or not args.prompt.read_text().strip():
        parser.error("prompt must be a nonempty local file")
    if any(
        not (LAB / "inputs" / f"{name}-{i}.wav").is_file()
        for name in args.cases
        for i in range(len(CASES[name]["turns"]))
    ):
        parser.error("missing synthetic inputs; run --prepare first")
    if not asyncio.run(run(args)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
