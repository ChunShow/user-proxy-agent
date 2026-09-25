"""Continuous Live audio with independent delegated work and paced carrier playback."""

import asyncio
import base64
import json
import re
import time
from uuid import uuid4

from agent_service.calls.media import audio_bytes, dtmf_message
from agent_service.calls.types import ProviderFailure


def has_sound(raw):
    # G.711 μ-law magnitude; used for local queue timing, never for transcription.
    total = 0
    for value in raw:
        value = (~value) & 255
        magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
        total += (magnitude - 132) ** 2
    return bool(raw) and total / len(raw) > 300**2


class LiveBridge:
    def __init__(self, model, *, listen_first=False, opening_message=""):
        self.model, self.listen_first, self.opening_message = model, listen_first, opening_message
        self.output = asyncio.Queue(250)
        self.buffer = bytearray()
        self.ready = asyncio.Event()
        self.send_lock, self.phone_lock = asyncio.Lock(), asyncio.Lock()
        self.pending, self.marks = {}, {}
        self.transcripts = []
        self.coordinator = None
        self.input_bytes = self.sent_bytes = self.played_bytes = 0
        self.error, self.ending = None, None
        self.last_input_sound = self.last_output_sound = 0.0
        self.last_digit_context = None
        self.last_input_text = ""

    async def send(self, event):
        async with self.send_lock:
            await self.model.send(json.dumps(event, ensure_ascii=False))

    async def command(self, kind, content, delegation_id=None):
        identity = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        expected = kind.removesuffix("append") + "appended"
        self.pending[identity] = (expected, future)
        try:
            await self.send(
                {
                    "type": kind,
                    "event_id": identity,
                    "delegation_id": delegation_id,
                    "content": content,
                }
            )
            await asyncio.wait_for(future, 10)
        finally:
            self.pending.pop(identity, None)

    async def receive(self):
        while True:
            event = json.loads(await self.model.recv())
            kind = event.get("type")
            if kind == "error":
                code = event.get("error", {}).get("code", "unknown")
                if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
                    code = "unknown"
                raise ProviderFailure("live_" + code)
            ack = self.pending.get(event.get("client_event_id"))
            if ack and kind == ack[0] and not ack[1].done():
                ack[1].set_result(None)
            if kind == "session.output_audio.delta":
                raw = audio_bytes(event.get("delta"), limit=240000)
                self.buffer.extend(raw)
                while len(self.buffer) >= 160:
                    packet = bytes(self.buffer[:160])
                    del self.buffer[:160]
                    self.output.put_nowait(packet)
            elif kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
                text = event.get("delta", "")
                if not isinstance(text, str) or len(text) > 8000:
                    raise ProviderFailure("invalid_live_transcript")
                role = "caller" if kind == "session.input_transcript.delta" else "assistant"
                part = {
                    "role": role,
                    "text": text,
                    "start_ms": event.get("start_ms"),
                    "end_ms": event.get("end_ms"),
                }
                self.transcripts.append(part)
                self.transcripts = self.transcripts[-300:]
                if role == "caller":
                    self.last_input_text = (self.last_input_text + text)[-1500:]
                if self.coordinator:
                    await self.coordinator.db(
                        self.coordinator.store.event, self.coordinator.call_id, "transcript", part
                    )
            elif kind == "session.delegation.created" and self.coordinator:
                delegation = event.get("delegation", {})
                if delegation.get("target") == "client":
                    await self.coordinator.request(delegation.get("id"))
            elif kind == "session.closed":
                return

    async def phone(self):
        async for event in self.media.events():
            kind = event.get("event")
            if kind == "start":
                self.ready.set()
            elif kind == "media":
                raw = audio_bytes(event["media"]["payload"])
                self.input_bytes += len(raw)
                if has_sound(raw):
                    self.last_input_sound = time.monotonic()
                    # A caller resuming during farewell cancels the automatic hangup.
                    if self.ending and time.monotonic() > self.ending["requested_at"] + 0.5:
                        self.ending = None
                await self.send(
                    {"type": "session.input_audio.append", "audio": event["media"]["payload"]}
                )
            elif kind == "mark":
                count = self.marks.pop(event.get("mark", {}).get("name"), None)
                if count is not None:
                    self.played_bytes = max(self.played_bytes, count)

    async def greet(self):
        await self.ready.wait()
        if not self.listen_first:
            await self.command(
                "session.instructions.append",
                "지금 한국어로 다음 시작 멘트를 말하고 상대의 답을 기다리세요: "
                + self.opening_message,
            )
        # This task must stay alive; normal greeting completion is not end of call.
        await asyncio.Future()

    async def play(self):
        await self.ready.wait()
        deadline = time.monotonic()
        while True:
            packet = await self.output.get()
            deadline = max(deadline, time.monotonic() - 0.1)
            async with self.phone_lock:
                await self.media.send(
                    {"event": "media", "media": {"payload": base64.b64encode(packet).decode()}}
                )
                self.sent_bytes += len(packet)
                if has_sound(packet):
                    self.last_output_sound = time.monotonic()
                    if self.ending:
                        self.ending["heard_output"] = True
                if self.sent_bytes % 800 == 0:
                    name = str(self.sent_bytes)
                    self.marks[name] = self.sent_bytes
                    if len(self.marks) > 100:
                        raise ProviderFailure("live_playback_unconfirmed")
                    await self.media.send({"event": "mark", "mark": {"name": name}})
            deadline += len(packet) / 8000
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def send_dtmf(self, digit):
        dtmf_message(digit)
        if not self.ready.is_set() or self.ending or not self.last_input_text:
            return {"error": "wait_for_prompt"}
        if (
            self.last_input_text == self.last_digit_context
            or time.monotonic() - self.last_input_sound < 0.6
        ):
            return {"error": "wait_for_new_prompt"}
        self.last_digit_context = self.last_input_text
        async with self.phone_lock:
            await self.media.send_dtmf(digit)
        return {"status": "sent", "digit": digit, "next_action": "listen"}

    async def end_call(self, reason, summary, *, spoken_result=""):
        if self.ending:
            return {"status": "already_pending"}
        if reason not in {"goal_achieved", "recipient_declined", "unable_to_continue"}:
            return {"error": "invalid_end_reason"}
        self.ending = {
            "reason": reason,
            "summary": summary[:1000],
            "status": "waiting_for_playback",
            "requested_at": time.monotonic(),
            "heard_output": False,
        }
        await self.command(
            "session.thinking.append", "백엔드 확인이 끝났습니다. 사용자 답변 대기를 마칩니다."
        )
        await self.command(
            "session.instructions.append",
            "먼저 다음 확인 결과를 한국어로 전달하세요: " + spoken_result + "\n"
            "그 뒤 짧게 통화를 마치는 인사를 하세요. "
            "새로운 질문을 하지 말고 인사 후에는 말하지 마세요.",
        )
        return {"status": "pending_farewell_playback"}

    async def deliver_result(self, result, delegation_id):
        await self.command(
            "session.thinking.append",
            "백엔드 확인이 끝났습니다. 사용자 답변 대기를 마칩니다.",
            delegation_id,
        )
        await self.command("session.commentary.append", result, delegation_id)
        await self.command(
            "session.instructions.append",
            "방금 백엔드 결과를 아직 말하지 않았다면 지금 상대에게 짧게 전달하세요. "
            "이미 말한 결과를 반복하거나 확인 대기 중이라고 말하지 마세요.",
        )

    async def finish(self):
        while True:
            await asyncio.sleep(0.1)
            end = self.ending
            if not end:
                continue
            elapsed = time.monotonic() - end["requested_at"]
            if (
                end["heard_output"]
                and time.monotonic() - self.last_output_sound > 2
                and time.monotonic() - self.last_input_sound > 2
                and self.output.empty()
                and self.played_bytes >= self.sent_bytes - 640
            ):
                # No utterance-done event: this is a timing heuristic, not verified speech.
                end["status"] = "audio_drained"
                return
            if elapsed > 25:
                end["status"] = "playback_unconfirmed"
                return

    async def _clear_output(self):
        while not self.output.empty():
            self.output.get_nowait()
        self.buffer.clear()
        self.marks.clear()
        async with self.phone_lock:
            await self.media.send({"event": "clear"})

    def report(self):
        return {
            "mode": "gpt_live",
            "error": self.error,
            "input_audio_bytes": self.input_bytes,
            "played_audio_ms": self.played_bytes // 8,
            "input_transcription_enabled": True,
            "end_call": self.ending,
        }

    async def run(self, media):
        self.media = media
        tasks = [
            asyncio.create_task(f())
            for f in (self.phone, self.receive, self.play, self.greet, self.finish)
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.coordinator:
                await self.coordinator.close()
            for _, future in self.pending.values():
                future.cancel()
        return self.report()
