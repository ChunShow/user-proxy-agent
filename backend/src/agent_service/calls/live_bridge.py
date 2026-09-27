"""Continuous Live audio with independent delegated work and paced carrier playback."""

import asyncio
import base64
import json
import re
import time
from uuid import uuid4

from agent_service.calls.interruption import Interruption
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
        self.transcript_version = self.input_revision = 0
        self.last_transcript_at = 0.0
        self.reviewed_version = self.review_count = 0
        self.last_review_at = 0.0
        self.coordinator = None
        self.input_bytes = self.sent_bytes = self.played_bytes = 0
        self.generated_bytes = 0
        self.cleared_unacked_bytes = self.cleared_through = 0
        self.interrupted = False
        self.last_progress = None
        self.error, self.ending = None, None
        self.last_input_sound = self.last_output_sound = 0.0
        self.last_generated_sound = 0.0
        self.pending_voice = self.voice_end_bytes = 0
        self.output_epoch = 0
        self.last_digit_context = None
        self.last_input_text = ""
        self.started_at = time.monotonic()
        self.timings = {}
        self.interruption = Interruption(self)

    def stamp(self, name):
        self.timings.setdefault(name, round((time.monotonic() - self.started_at) * 1000))

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
                self.stamp("output_audio_first_ms")
                raw = audio_bytes(event.get("delta"), limit=240000)
                self.generated_bytes += len(raw)
                self.buffer.extend(raw)
                while len(self.buffer) >= 160:
                    packet = bytes(self.buffer[:160])
                    del self.buffer[:160]
                    if not self.interruption.accept_output(packet, time.monotonic()):
                        continue
                    self.output.put_nowait(packet)
                    if has_sound(packet):
                        self.pending_voice += 1
                        self.last_generated_sound = time.monotonic()
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
                if text.strip():
                    self.transcript_version += 1
                    self.last_transcript_at = time.monotonic()
                self.transcripts.append(part)
                self.transcripts = self.transcripts[-300:]
                if role == "caller":
                    self.stamp("input_transcript_first_ms")
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
                self.stamp("input_audio_first_ms")
                raw = audio_bytes(event["media"]["payload"])
                self.input_bytes += len(raw)
                if has_sound(raw):
                    self.input_revision += 1
                    self.last_input_sound = time.monotonic()
                    # A caller resuming during farewell cancels the automatic hangup.
                    if self.ending and time.monotonic() > self.ending["requested_at"] + 0.5:
                        self.ending = None
                        await self.audit("end_canceled", {"reason": "caller_audio_resumed"})
                await self.send(
                    {"type": "session.input_audio.append", "audio": event["media"]["payload"]}
                )
                self.interruption.detector.feed(raw, time.monotonic())
                await self.interruption.check_input(time.monotonic())
            elif kind == "mark":
                count = self.marks.pop(event.get("mark", {}).get("name"), None)
                if count is not None:
                    self.stamp("playback_ack_first_ms")
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
        quiet_seconds, paced_epoch, reserved_generation = 0.0, -1, -1
        while True:
            packet = await self.output.get()
            epoch = self.output_epoch
            voiced = has_sound(packet)
            # Build a small jitter reserve at startup or in established quiet only.
            # After startup, refill low-energy packets only; preserve every sample.
            # No lock is held while waiting, so clear and cancellation stay responsive.
            if epoch != paced_epoch or (
                not voiced
                and quiet_seconds >= 0.2
                and self.output.qsize() < 3
                and self.generated_bytes != reserved_generation
            ):
                started = time.monotonic()
                until = started + 0.12
                for _ in range(12):
                    if self.output.qsize() >= 6 or epoch != self.output_epoch:
                        break
                    remaining = until - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.01, remaining))
                reserved_generation = self.generated_bytes
                if time.monotonic() > started:
                    # Intentional reserve must not be immediately spent by catch-up bursts.
                    deadline = time.monotonic()
            deadline = max(deadline, time.monotonic() - 0.1)
            async with self.phone_lock:
                if epoch != self.output_epoch or self.interruption.blocked:
                    continue
                await self.media.send(
                    {"event": "media", "media": {"payload": base64.b64encode(packet).decode()}}
                )
                self.stamp("audio_sent_first_ms")
                self.sent_bytes += len(packet)
                self.interruption.detector.played(packet, time.monotonic())
                paced_epoch = epoch
                quiet_seconds = 0.0 if voiced else quiet_seconds + len(packet) / 8000
                if voiced:
                    self.pending_voice = max(0, self.pending_voice - 1)
                    self.voice_end_bytes = self.sent_bytes
                    self.last_output_sound = time.monotonic()
                    if self.ending:
                        self.ending["heard_output"] = True
                # Mark the last short packet as well as periodic 100ms boundaries.
                # Keep network writes out of finish(), so a stalled send cannot
                # suspend the independent hangup deadline.
                tail_needs_mark = (
                    self.ending
                    and self.output.empty()
                    and self.voice_end_bytes > self.played_bytes
                    and not any(count >= self.voice_end_bytes for count in self.marks.values())
                )
                if self.sent_bytes % 800 == 0 or tail_needs_mark:
                    await self._mark_playback()
            deadline += len(packet) / 8000
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def _mark_playback(self):
        # Caller holds phone_lock, so the mark follows all bytes it acknowledges.
        name = (
            f"{self.output_epoch}:{self.sent_bytes}" if self.output_epoch else str(self.sent_bytes)
        )
        self.marks[name] = self.sent_bytes
        if len(self.marks) > 100:
            raise ProviderFailure("live_playback_unconfirmed")
        await self.media.send({"event": "mark", "mark": {"name": name}})

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

    async def end_call(self, reason, summary, *, spoken_result="", farewell_already_said=False):
        if self.interruption.blocked:
            return {"error": "caller_speaking"}
        if self.ending:
            return {"status": "already_pending"}
        if reason not in {
            "goal_achieved",
            "recipient_declined",
            "recipient_requested_end",
            "unable_to_continue",
        }:
            return {"error": "invalid_end_reason"}
        self.ending = {
            "reason": reason,
            "summary": summary[:1000],
            "status": "waiting_for_playback",
            "requested_at": time.monotonic(),
            "heard_output": bool(
                farewell_already_said
                and (self.voice_end_bytes or self.pending_voice or has_sound(self.buffer))
            ),
            "farewell_already_said": farewell_already_said,
        }
        await self.audit(
            "end_requested",
            {
                "reason": reason,
                "farewell_already_said": farewell_already_said,
            },
        )
        started = time.monotonic()
        await self.audit("farewell_commands_started", {})
        try:
            await self.command(
                "session.thinking.append", "백엔드 확인이 끝났습니다. 사용자 답변 대기를 마칩니다."
            )
            await self.command(
                "session.instructions.append",
                (
                    "필요한 안내와 마지막 인사는 이미 전달됐습니다. 이제 말하지 마세요. "
                    "추가 설명·업무 결과 요약·인사·질문을 반복하지 마세요. "
                    "서버가 재생 확인 후 종료합니다."
                    if farewell_already_said
                    else "종료가 승인됐습니다. 다음 내용 중 아직 말하지 않은 필수 안내와 "
                    "짧은 인사를 "
                    "한 번의 발화로 자연스럽게 전달하세요. 이미 전달한 정보는 생략하고 "
                    "인사가 포함돼 있으면 별도 인사를 덧붙이지 마세요. "
                    "통화 결과를 요청자에게 보고하듯 설명하지 말고 전화 상대에게 직접 말하세요. "
                    "새로운 질문을 하지 말고 인사 후에는 말하지 마세요.\n"
                    "상대에게 전달할 내용:\n" + spoken_result
                ),
            )
        except Exception:
            await self.audit("farewell_commands_failed", {})
            raise
        await self.audit(
            "farewell_commands_acked", {"elapsed_ms": round((time.monotonic() - started) * 1000)}
        )
        return {"status": "pending_farewell_playback"}

    async def deliver_result(self, result, delegation_id):
        if self.interruption.blocked:
            return
        speech_epoch = self.interruption.count
        await self.command(
            "session.thinking.append",
            "백엔드 확인이 끝났습니다. 사용자 답변 대기를 마칩니다.",
            delegation_id,
        )
        if self.interruption.blocked or self.interruption.count != speech_epoch:
            return
        await self.command("session.commentary.append", result, delegation_id)
        if self.interruption.blocked or self.interruption.count != speech_epoch:
            return
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
            now = time.monotonic()
            elapsed = now - end["requested_at"]
            quiet_output = now - max(self.last_output_sound, self.last_generated_sound)
            quiet_input = now - self.last_input_sound
            drained = (
                end["heard_output"]
                and quiet_output > 2
                and quiet_input > 2
                and self.pending_voice == 0
                and not has_sound(self.buffer)
                and self.voice_end_bytes > 0
                and self.played_bytes >= self.voice_end_bytes
            )
            if drained or elapsed > 25:
                # Activity + carrier ACK is a heuristic, not an utterance-done event.
                end["status"] = "audio_drained" if drained else "playback_unconfirmed"
                end["playback"] = {
                    "wait_ms": round(elapsed * 1000),
                    "voice_end_bytes": self.voice_end_bytes,
                    "acked_bytes": self.played_bytes,
                    "pending_voice_packets": self.pending_voice,
                    "queued_packets": self.output.qsize(),
                    "buffer_bytes": len(self.buffer),
                    "output_quiet_ms": round(quiet_output * 1000),
                    "input_quiet_ms": round(quiet_input * 1000),
                }
                await self.audit(
                    "end_playback_finished", {"status": end["status"], **end["playback"]}
                )
                # Saving diagnostics yields to incoming audio. Recheck before exiting.
                if self.ending is not end:
                    continue
                if drained and (
                    self.pending_voice
                    or has_sound(self.buffer)
                    or self.played_bytes < self.voice_end_bytes
                    or time.monotonic()
                    - max(self.last_input_sound, self.last_output_sound, self.last_generated_sound)
                    <= 2
                ):
                    end["status"] = "waiting_for_playback"
                    continue
                return

    async def audit(self, kind, content):
        if self.coordinator:
            try:
                await self.coordinator.db(
                    self.coordinator.store.event, self.coordinator.call_id, kind, content
                )
            except Exception:
                pass  # Diagnostics must not break audio or direct hangup.

    async def review_tick(self):
        now = time.monotonic()
        if (
            not self.coordinator
            or self.interruption.blocked
            or self.ending
            or self.review_count >= 12
            or self.transcript_version <= self.reviewed_version
            or now - self.started_at < 10
            or now - self.last_review_at < 10
            or now
            - max(
                self.last_input_sound,
                self.last_output_sound,
                self.last_generated_sound,
                self.last_transcript_at,
            )
            < 3
            or self.pending_voice
            or has_sound(self.buffer)
            or not any(p["role"] == "caller" and p["text"].strip() for p in self.transcripts)
            or not any(p["role"] == "assistant" and p["text"].strip() for p in self.transcripts)
        ):
            return False
        version = self.transcript_version
        accepted = await self.coordinator.review_completion(version)
        if accepted:
            self.reviewed_version = version
            self.review_count += 1
            self.last_review_at = now
        return accepted

    async def review(self):
        while True:
            await asyncio.sleep(0.5)
            try:
                await self.review_tick()
            except Exception:
                # Backoff transient store errors without disrupting the media tasks.
                self.last_review_at = time.monotonic()

    async def save_progress(self, *, force=False):
        if not self.coordinator:
            return
        snapshot = {
            "generated_bytes": self.generated_bytes,
            "sent_bytes": self.sent_bytes,
            "playback_acked_bytes": self.played_bytes,
            "interrupted": self.interrupted,
            "barge_in_count": self.interruption.count,
            "dropped_audio_bytes": self.interruption.dropped_bytes,
            "cleared_unacked_bytes": self.cleared_unacked_bytes,
        }
        if not force and snapshot == self.last_progress:
            return
        try:
            await self.coordinator.db(
                self.coordinator.store.event, self.coordinator.call_id, "audio_progress", snapshot
            )
        except Exception:
            # Display telemetry must not terminate or block the audio sending task.
            return
        self.last_progress = snapshot

    async def progress(self):
        while True:
            await asyncio.sleep(1)
            await self.save_progress()

    async def _clear_output(self, *, interrupted=False):
        if self.ending and self.ending["status"] == "waiting_for_playback":
            await self.audit("end_canceled", {"reason": "output_cleared"})
        async with self.phone_lock:
            self.cleared_unacked_bytes += max(
                0, self.sent_bytes - max(self.played_bytes, self.cleared_through)
            )
            self.cleared_through = self.sent_bytes
            self.interruption.dropped_bytes += max(
                0, self.generated_bytes - self.sent_bytes - self.interruption.dropped_bytes
            )
            self.output_epoch += 1
            self.ending = None
            self.pending_voice = self.voice_end_bytes = 0
            while not self.output.empty():
                self.output.get_nowait()
            self.buffer.clear()
            self.marks.clear()
            await self.media.send({"event": "clear"})
        if interrupted:
            self.interrupted = True
            await self.save_progress(force=True)

    def report(self):
        return {
            "mode": "gpt_live",
            "error": self.error,
            "input_audio_bytes": self.input_bytes,
            "played_audio_ms": max(0, self.played_bytes - self.cleared_unacked_bytes) // 8,
            "interruptions": self.interruption.count,
            "input_transcription_enabled": True,
            "end_call": self.ending,
            "timings_ms": self.timings,
        }

    async def run(self, media):
        self.media = media
        tasks = [
            asyncio.create_task(f())
            for f in (
                self.phone,
                self.receive,
                self.play,
                self.greet,
                self.finish,
                self.progress,
                self.review,
                self.interruption.run,
            )
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
            await self.interruption.close()
            await self.save_progress(force=True)
            if self.coordinator:
                await self.coordinator.close()
            for _, future in self.pending.values():
                future.cancel()
        return self.report()
