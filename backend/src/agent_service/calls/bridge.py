"""Full-duplex raw audio relay, server VAD, paced playout and history truncation."""

import asyncio
import base64
import json
import re

from agent_service.calls.media import audio_bytes
from agent_service.calls.types import ProviderFailure
from agent_service.calls.voice_tools import parse_dtmf, parse_end_call


class NativeAudioBridge:
    def __init__(self, model, *, end_call_timeout=35, listen_first=False, opening_message=""):
        self.opening_message = opening_message
        self.model = model
        self.epoch = 0
        self.output = asyncio.Queue(1500)  # At most 30s of model audio awaiting playout.
        self.ready = asyncio.Event()
        self.phone_lock = asyncio.Lock()
        self.response_id = None
        self.blocked = set()
        self.items = {}
        self.current = None
        self.pending_marks = {}
        self.input_bytes = 0
        self.speech_turns = 0
        self.interruptions = 0
        self.error = None
        self.model_responses = []
        self.end_call_timeout = end_call_timeout
        self.ending = None
        self.end_calls = []
        self.handled_tools = set()
        self.end_changed = asyncio.Event()
        self.listen_first = listen_first
        self.speaking = False
        self.last_dtmf_turn = 0
        self.dtmf_actions = []

    def report(self):
        ledger = []
        for item_id, item in self.items.items():
            ledger.append(
                {
                    "item_id": item_id,
                    "text": item["text"],
                    "generated_audio_ms": item["generated"] // 8,
                    "played_audio_ms": item["played"] // 8,
                    "status": "interrupted"
                    if item["interrupted"]
                    else "played"
                    if item["done"] and item["played"] >= item["generated"]
                    else "unconfirmed",
                }
            )
        return {
            "status": "failed" if self.error else "completed",
            "error": self.error,
            "mode": "native_audio",
            "input_audio_bytes": self.input_bytes,
            "user_speech_turns": self.speech_turns,
            "interruptions": self.interruptions,
            "played_audio_ms": sum(x["played"] for x in self.items.values()) // 8,
            "ledger": ledger,
            "history": [
                {"role": "assistant", "content": x["text"]}
                for x in ledger
                if x["status"] == "played" and x["text"]
            ],
            "input_transcription_enabled": False,
            "model_responses": list(self.model_responses),
            # These are model-reported reasons, not independent proof of goal completion.
            "end_call": self._end_report(self.end_calls[-1]) if self.end_calls else None,
            "end_call_requests": [self._end_report(end) for end in self.end_calls],
            "dtmf_actions": list(self.dtmf_actions),
        }

    @staticmethod
    def _end_report(end):
        return {k: end[k] for k in ("call_id", "reason", "summary", "status")}

    async def _send_digit(self, call_id, arguments):
        args = parse_dtmf(arguments)
        if not self.ready.is_set() or self.speaking or self.speech_turns <= self.last_dtmf_turn:
            raise ValueError("wait_for_new_prompt")
        if self.ending:
            raise ValueError("call_ending")
        await self._clear_output(block_response=False)
        self.last_dtmf_turn = self.speech_turns
        action = {"call_id": call_id, **args, "status": "delivery_unknown"}
        self.dtmf_actions.append(action)
        try:
            async with self.phone_lock:
                await self.media.send_dtmf(args["digits"])
        except Exception:
            return {"error": "clawops_dtmf_delivery_unknown", "retry": False}
        action["status"] = "sent"
        return {"status": "sent", "digits": args["digits"], "next_action": "listen"}

    async def _tools(self, response):
        if response.get("id") != self.response_id or self.response_id in self.blocked:
            return
        followup = False
        calls = [x for x in response.get("output", []) if x.get("type") == "function_call"]
        for item in calls:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in self.handled_tools:
                continue
            self.handled_tools.add(call_id)
            try:
                if len(calls) != 1:
                    raise ValueError("one_action_per_response")
                if item.get("name") == "send_dtmf":
                    output = await self._send_digit(call_id, item.get("arguments"))
                elif item.get("name") == "end_call":
                    args = parse_end_call(item.get("arguments"))
                    if self.ending:
                        output = {"status": "already_pending"}
                    else:
                        self.ending = {
                            **args,
                            "call_id": call_id,
                            "status": "waiting_for_playback",
                            "response_id": None,
                            "completed": False,
                            "deadline": asyncio.get_running_loop().time() + self.end_call_timeout,
                        }
                        self.end_calls.append(self.ending)
                        self.end_changed.set()
                        output = {"status": "pending_farewell_playback"}
                        followup = True
                else:
                    raise ValueError("unknown_tool")
            except ValueError as exc:
                output = {"error": str(exc)}
                followup = len(calls) == 1 and item.get("name") != "send_dtmf"
            await self.send_model(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(output),
                    },
                }
            )
            if output.get("error") == "clawops_dtmf_delivery_unknown":
                raise ProviderFailure("clawops_dtmf_delivery_unknown")
        if followup:
            response = {}
            if self.ending:
                response = {
                    "metadata": {"end_call_id": self.ending["call_id"]},
                    "tool_choice": "none",
                    "instructions": (
                        "한국어로 마지막 인사만 짧게 말하세요: "
                        "네, 알겠습니다. 통화를 마치겠습니다. 감사합니다. "
                        "새로운 질문이나 정보는 추가하지 마세요."
                    ),
                }
            await self.send_model({"type": "response.create", "response": response})

    def _cancel_ending(self, status):
        if self.ending:
            self.ending["status"] = status
            self.ending = None
            self.end_changed.set()

    async def _finish(self):
        while True:
            self.end_changed.clear()
            end = self.ending
            if end is None:
                await self.end_changed.wait()
                continue
            items = [x for x in self.items.values() if x["response_id"] == end["response_id"]]
            if (
                end["completed"]
                and items
                and all(
                    x["done"]
                    and x["generated"] > 0
                    and x["played"] >= x["generated"]
                    and not x["interrupted"]
                    for x in items
                )
            ):
                end["status"] = "played"
                # Returning releases the bridge; CallManager owns REST hangup + lookup.
                return
            remaining = end["deadline"] - asyncio.get_running_loop().time()
            try:
                await asyncio.wait_for(self.end_changed.wait(), max(0, remaining))
            except TimeoutError:
                end["status"] = "playback_timeout"
                raise ProviderFailure("end_call_playback_timeout") from None

    def _record_response(self, response):
        details = response.get("status_details") or {}
        error = details.get("error") or {}
        usage = response.get("usage") or {}

        def code(value):
            # Retain diagnostic identifiers, never provider messages or payloads.
            return (
                value
                if isinstance(value, str) and re.fullmatch(r"[a-z0-9_]{1,80}", value)
                else None
            )

        self.model_responses.append(
            {
                "status": code(response.get("status")),
                "reason": code(details.get("reason")),
                "error_code": code(error.get("code")),
                "output_tokens": usage.get("output_tokens")
                if isinstance(usage.get("output_tokens"), int)
                else None,
            }
        )

    async def send_model(self, event):
        await self.model.send(json.dumps(event, ensure_ascii=False))

    async def _phone(self):
        async for event in self.media.events():
            kind = event.get("event")
            if kind == "start":
                self.ready.set()
                if self.listen_first:
                    continue
                await self.send_model(
                    {
                        "type": "response.create",
                        "response": {
                            "tool_choice": "none",
                            "instructions": (
                                ("한국어로 다음 시작 멘트를 말하세요: " + self.opening_message)
                                if self.opening_message
                                else "한국어로 자신이 AI 통화 도우미라고 짧게 인사하고 "
                                "잘 들리는지 물어보세요."
                            ),
                        },
                    }
                )
            elif kind == "media":
                payload = event["media"]["payload"]
                self.input_bytes += len(audio_bytes(payload))
                await self.send_model({"type": "input_audio_buffer.append", "audio": payload})
            elif kind == "mark":
                mark = self.pending_marks.pop(event.get("mark", {}).get("name"), None)
                if mark:
                    epoch, item_id, count = mark
                    if epoch == self.epoch and not self.items[item_id]["interrupted"]:
                        self.items[item_id]["played"] = max(self.items[item_id]["played"], count)
                        self.end_changed.set()

    async def _interrupt(self):
        self._cancel_ending("interrupted")
        self.speech_turns += 1
        self.speaking = True
        await self._clear_output()

    async def _clear_output(self, *, block_response=True):
        self.epoch += 1
        if self.response_id and block_response:
            self.blocked.add(self.response_id)
        while not self.output.empty():
            self.output.get_nowait()
        self.pending_marks.clear()
        async with self.phone_lock:
            await self.media.send({"event": "clear"})
        if self.current and not self.items[self.current]["interrupted"]:
            item = self.items[self.current]
            if not item["done"] or item["played"] < item["generated"]:
                item["interrupted"] = True
                item["buffer"].clear()
                self.interruptions += 1
                # Only remotely acknowledged audio is retained in the model's history.
                await self.send_model(
                    {
                        "type": "conversation.item.truncate",
                        "item_id": self.current,
                        "content_index": item["content_index"],
                        "audio_end_ms": item["played"] // 8,
                    }
                )

    async def _model(self):
        while True:
            event = json.loads(await self.model.recv())
            kind = event.get("type")
            if kind == "error":
                self._record_response(
                    {"status": "error", "status_details": {"error": event.get("error") or {}}}
                )
                raise ProviderFailure("azure_audio_failed")
            if kind == "input_audio_buffer.speech_started":
                await self._interrupt()
            elif kind == "input_audio_buffer.speech_stopped":
                self.speaking = False
            elif kind == "response.created":
                response = event["response"]
                self.response_id = response["id"]
                end_id = (response.get("metadata") or {}).get("end_call_id")
                if end_id:
                    if self.ending and self.ending["call_id"] == end_id:
                        self.ending["response_id"] = self.response_id
                    else:
                        # Speech can cancel ending before its response.created arrives.
                        self.blocked.add(self.response_id)
            elif kind in (
                "response.output_audio.delta",
                "response.output_audio.done",
                "response.output_audio_transcript.done",
            ):
                response = event["response_id"]
                if response in self.blocked:
                    continue
                if response != self.response_id:
                    raise ProviderFailure("azure_audio_response_mismatch")
                item_id = event["item_id"]
                item = self.items.setdefault(
                    item_id,
                    {
                        "generated": 0,
                        "sent": 0,
                        "played": 0,
                        "buffer": bytearray(),
                        "text": "",
                        "done": False,
                        "interrupted": False,
                        "content_index": event.get("content_index", 0),
                        "response_id": response,
                    },
                )
                self.current = item_id
                if item["interrupted"]:
                    continue
                if kind == "response.output_audio.delta":
                    raw = audio_bytes(event["delta"], limit=240000)
                    item["generated"] += len(raw)
                    if item["generated"] > 240000:
                        raise ProviderFailure("azure_audio_response_too_long")
                    item["buffer"].extend(raw)
                    while len(item["buffer"]) >= 160:
                        packet = bytes(item["buffer"][:160])
                        del item["buffer"][:160]
                        self.output.put_nowait((self.epoch, item_id, packet))
                elif kind == "response.output_audio.done":
                    if item["buffer"]:
                        self.output.put_nowait((self.epoch, item_id, bytes(item["buffer"])))
                        item["buffer"].clear()
                    item["done"] = True
                    self.output.put_nowait((self.epoch, item_id, None))
                else:
                    item["text"] = event.get("transcript", "")[:8000]
            elif kind == "response.done":
                response = event["response"]
                self._record_response(response)
                if response.get("id") in self.blocked:
                    continue
                if self.ending and response.get("id") == self.ending["response_id"]:
                    if response["status"] == "completed":
                        self.ending["completed"] = True
                        self.end_changed.set()
                    else:
                        self._cancel_ending("response_not_completed")
                if response["status"] == "completed":
                    await self._tools(response)
                details = response.get("status_details") or {}
                if (
                    response["status"] == "incomplete"
                    and details.get("reason") == "max_output_tokens"
                ):
                    # This ends one answer, not the call. Preserve queued audio and
                    # keep receiving the next user turn; never auto-repeat the answer.
                    continue
                if response["status"] not in ("completed", "cancelled"):
                    raise ProviderFailure("azure_audio_response_failed")

    async def _mark(self, epoch, item_id):
        count = self.items[item_id]["sent"]
        name = f"{epoch}-{item_id}-{count}"
        self.pending_marks[name] = (epoch, item_id, count)
        await self.media.send({"event": "mark", "mark": {"name": name}})

    async def _play(self):
        await self.ready.wait()
        while True:
            epoch, item_id, packet = await self.output.get()
            async with self.phone_lock:
                if epoch != self.epoch:
                    continue
                item = self.items[item_id]
                if packet is None:
                    await self._mark(epoch, item_id)
                    continue
                payload = base64.b64encode(packet.ljust(160, b"\xff")).decode()
                await self.media.send({"event": "media", "media": {"payload": payload}})
                item["sent"] += len(packet)
                if item["sent"] % 800 == 0:
                    await self._mark(epoch, item_id)
            await asyncio.sleep(0.02)

    async def run(self, media):
        self.media = media
        tasks = [
            asyncio.create_task(self._phone()),
            asyncio.create_task(self._model()),
            asyncio.create_task(self._play()),
            asyncio.create_task(self._finish()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            self.error = "native_audio_cancelled"
            raise
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ProviderFailure) else "native_audio_failed"
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.ending and self.ending["status"] == "waiting_for_playback":
                self._cancel_ending("not_played")
            # The carrier owner terminates the call after this returns.
        return self.report()
