"""Lifespan-owned call work; HTTP/chat cancellation never owns the carrier call."""

import asyncio
import json
import re
import sqlite3
from contextlib import asynccontextmanager

import httpx
from starlette.concurrency import run_in_threadpool

from agent_service.calls.bridge import NativeAudioBridge
from agent_service.calls.carrier import ClawOpsControl
from agent_service.calls.connection import AgentConnection
from agent_service.calls.delegation import DelegationCoordinator
from agent_service.calls.live import LiveAudioSession
from agent_service.calls.live_bridge import LiveBridge
from agent_service.calls.live_store import LiveStore
from agent_service.calls.preflight import check_local_sender
from agent_service.calls.realtime import AzureAudioSession
from agent_service.calls.settings import CallSettings, normalize_number
from agent_service.calls.store import TERMINAL
from agent_service.calls.types import TERMINAL as CARRIER_TERMINAL
from agent_service.calls.types import DialRejected, DialUncertain, ProviderFailure
from agent_service.storage import StoreError


class Gateway(ClawOpsControl):
    def __init__(self, settings, client):
        super().__init__(settings.carrier, client)
        self.config = settings

    async def preflight(self):
        await check_local_sender()
        return await super().preflight()

    def audio(self, spec):
        if self.config.audio_mode == "live":
            return LiveAudioSession(
                self.config.realtime_base_url,
                self.config.realtime_api_key,
                self.config.live_model,
                voice=self.config.live_voice,
                task=json.dumps(spec.model_dump(exclude={"destination"}), ensure_ascii=False),
            )
        return AzureAudioSession(
            self.config.realtime_base_url,
            self.config.realtime_api_key,
            self.config.realtime_model,
            task=json.dumps(spec.model_dump(exclude={"destination"}), ensure_ascii=False),
        )

    def connection(self):
        return AgentConnection(self.settings)

    def bridge(self, model, spec):
        if self.config.audio_mode == "live":
            return LiveBridge(
                model, listen_first=spec.listen_first, opening_message=spec.opening_message
            )
        return NativeAudioBridge(
            model, listen_first=spec.listen_first, opening_message=spec.opening_message
        )


@asynccontextmanager
async def open_gateway(settings):
    async with httpx.AsyncClient(trust_env=False) as client:
        yield Gateway(settings, client)


def requested_number(current, texts, destination):
    # The LLM resolves intent, while the server requires an explicit call imperative
    # in this user turn and a literal number in user-authored conversation text.
    # Match a prohibition of the call action, not a constraint on the conversation
    # such as "통화 가능한지는 추측하지 말고 확인해줘".
    if re.search(
        r"(?:전화|통화)(?:를|는|도|은)?\s*"
        r"(?:(?:절대|다시|아직|지금|오늘|내일|당장은|이제|함부로)\s*)*"
        r"(?:(?:연결|시작)?\s*하지|걸지|걸어\s*주지|해\s*주지|안\s*(?:걸|해)|말아|말고|금지)",
        current,
    ):
        return False
    if not re.search(
        r"(전화|통화).{0,80}(걸어|줘|주세요|해\s*주|진행|시작|부탁|해라)", current, re.S
    ) and not re.search(r"\bcall\b.{0,40}\b(please|now)\b", current, re.I):
        return False
    for text in texts:
        for candidate in re.findall(r"(?<![\d+])(?:\+82[ ()-]*|0|1)[\d ()-]{6,24}\d(?!\d)", text):
            try:
                if normalize_number(candidate.strip()) == destination:
                    return True
            except ValueError:
                pass
    return False


class CallManager:
    def __init__(
        self,
        store,
        *,
        settings_loader=CallSettings.load,
        gateway_factory=open_gateway,
        poll_seconds=2,
        confirmation_attempts=5,
        max_seconds_override=None,
    ):
        self.store, self.settings_loader, self.gateway_factory = (
            store,
            settings_loader,
            gateway_factory,
        )
        self.poll_seconds, self.confirmation_attempts = poll_seconds, confirmation_attempts
        self.max_seconds_override = max_seconds_override
        self.tasks, self.signals, self.locks = {}, {}, {}
        self.submissions = set()
        self.reports = {}
        self.recoveries = set()
        self.closing = False

    async def db(self, method, *args, **kwargs):
        return await run_in_threadpool(method, *args, **kwargs)

    def _spawn(self, call_id, coroutine, *, recovery=False):
        if call_id in self.tasks:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine, name="managed-phone-call")
        self.tasks[call_id] = task
        if recovery:
            self.recoveries.add(call_id)

        def finished(done):
            self.tasks.pop(call_id, None)
            self.recoveries.discard(call_id)
            # A DB failure leaves the durable active slot for restart recovery.
            # Consume exceptions here without logging provider/user payloads.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)

    async def start(self, owner, conversation_id, source_user_message_id, spec):
        if self.closing:
            raise StoreError("calls_unavailable", 503)
        # Own the complete reservation+schedule operation, including thread completion.
        return await self._owned(
            self._register(owner, conversation_id, source_user_message_id, spec)
        )

    async def _owned(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.submissions.add(task)

        def finished(done):
            self.submissions.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _register(self, owner, cid, uid, spec):
        settings = self.settings_loader()
        if spec.destination not in settings.allowed_numbers:
            raise StoreError("call_number_not_allowed", 422)
        current, texts = await self.db(self.store.user_texts, owner, cid, uid)
        if not requested_number(current, texts, spec.destination):
            raise StoreError("call_request_required", 422)
        call = await self.db(self.store.register, owner, cid, uid, spec)
        if call["status"] == "preparing":
            self.signals.setdefault(call["id"], asyncio.Event())
            self._spawn(call["id"], self._run(call["id"], settings, spec))
        return call

    async def get(self, owner, call_id):
        return await self.db(self.store.get, owner, call_id)

    async def stop(self, owner, call_id):
        return await self._owned(self._stop(owner, call_id))

    async def _stop(self, owner, call_id):
        call = await self.db(self.store.request_stop, owner, call_id)
        await self.db(LiveStore(self.store).cancel, call_id)
        if call["status"] not in TERMINAL:
            self.signals.setdefault(call_id, asyncio.Event()).set()
            if call_id not in self.tasks:
                self._spawn(call_id, self._recover_one(call_id), recovery=True)
        return call

    async def _watch(self, gateway, call_id, external_id):
        while True:
            try:
                snapshot = await gateway.lookup(external_id)
                if snapshot.status in CARRIER_TERMINAL:
                    return snapshot
                if snapshot.status == "active":
                    await self.db(self.store.update, call_id, status="connected", error_code=None)
            except ProviderFailure:
                await self.db(self.store.update, call_id, error_code="call_status_unavailable")
            await asyncio.sleep(self.poll_seconds)

    async def _conversation(self, gateway, connection, model, call_id, external_id, spec):
        async with connection.media(external_id) as media:
            await self.db(self.store.update, call_id, status="connected")
            bridge = gateway.bridge(model, spec)
            if isinstance(bridge, LiveBridge):
                bridge.coordinator = DelegationCoordinator(self.store, call_id, bridge)
            try:
                return await bridge.run(media)
            finally:
                if isinstance(bridge, (NativeAudioBridge, LiveBridge)):
                    self.reports[call_id] = bridge.report()
                # Best effort clear before closing the media socket on direct stop/timeout.
                if isinstance(bridge, (NativeAudioBridge, LiveBridge)):
                    try:
                        if isinstance(bridge, LiveBridge):
                            row = await self.db(self.store.record, call_id)
                            await bridge._clear_output(interrupted=bool(row["stop_requested"]))
                        else:
                            await bridge._clear_output()
                    except Exception:
                        pass

    async def _connected(self, gateway, connection, model, call_id, external_id, spec, seconds):
        tasks = [
            asyncio.create_task(
                self._conversation(gateway, connection, model, call_id, external_id, spec)
            ),
            asyncio.create_task(self.signals[call_id].wait()),
            asyncio.create_task(self._watch(gateway, call_id, external_id)),
        ]
        try:
            done, _ = await asyncio.wait(
                tasks, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                await self.db(self.store.update, call_id, error_code="call_time_limit")
            if tasks[0] in done:
                return tasks[0].result()
            if tasks[2] in done:
                tasks[2].result()
            return {}
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, call_id, settings, spec):
        external_id, report = None, {}
        async with self.gateway_factory(settings) as gateway:
            try:
                await gateway.preflight()
                async with gateway.audio(spec) as model, gateway.connection() as connection:
                    if not await self.db(self.store.claim_dial, call_id):
                        await self.db(
                            self.store.update, call_id, status="canceled", outcome="canceled"
                        )
                        return
                    deadline = asyncio.get_running_loop().time() + (
                        self.max_seconds_override or settings.max_seconds
                    )
                    snapshot = await gateway.dial(call_id, spec.destination)
                    external_id = snapshot.external_id
                    await self.db(self.store.update, call_id, provider_call_id=external_id)
                    current = await self.db(self.store.record, call_id)
                    if snapshot.status not in CARRIER_TERMINAL and not current["stop_requested"]:
                        seconds = max(0, deadline - asyncio.get_running_loop().time())
                        report = await self._connected(
                            gateway, connection, model, call_id, external_id, spec, seconds
                        )
                        if report.get("error"):
                            await self.db(
                                self.store.update, call_id, error_code="call_audio_failed"
                            )
            except DialRejected:
                await self.db(
                    self.store.update,
                    call_id,
                    status="failed",
                    outcome="incomplete",
                    error_code="call_rejected",
                )
            except DialUncertain:
                await self.db(
                    self.store.update, call_id, status="unknown", error_code="call_delivery_unknown"
                )
            except Exception:
                row = await self.db(self.store.record, call_id)
                if not external_id:
                    uncertain = row["dial_attempted_at"] is not None
                    await self.db(
                        self.store.update,
                        call_id,
                        status="unknown" if uncertain else "failed",
                        outcome="pending" if uncertain else "incomplete",
                        error_code="call_delivery_unknown" if uncertain else "call_setup_failed",
                    )
                else:
                    await self.db(self.store.update, call_id, error_code="call_audio_failed")
            finally:
                if external_id:
                    # Even if persistence after dial failed, retain the in-memory ID for cleanup.
                    try:
                        await self._finish(
                            gateway, call_id, external_id, report or self.reports.get(call_id, {})
                        )
                    except sqlite3.Error:
                        # Database outage cannot disable the direct carrier cleanup path.
                        try:
                            await gateway.hangup(external_id)
                        except ProviderFailure:
                            pass
                        raise
                    finally:
                        self.reports.pop(call_id, None)

    async def _finish(self, gateway, call_id, external_id, report=None):
        lock = self.locks.setdefault(call_id, asyncio.Lock())
        async with lock:
            row = await self.db(self.store.record, call_id)
            if row["status"] in TERMINAL:
                return
            end = (report or {}).get("end_call") or json.loads(row["end_report"])
            if report:
                metrics = {
                    k: report.get(k, 0)
                    for k in (
                        "input_audio_bytes",
                        "user_speech_turns",
                        "interruptions",
                        "played_audio_ms",
                    )
                }
                metrics["input_transcription_enabled"] = bool(
                    report.get("input_transcription_enabled")
                )
                metrics["timings_ms"] = report.get("timings_ms", {})
                await self.db(
                    self.store.update,
                    call_id,
                    end_report=json.dumps(end),
                    metrics=json.dumps(metrics),
                )
            verified = end.get("status") == "played"
            outcome = (
                "canceled"
                if row["stop_requested"]
                else "model_reported_success"
                if verified and end.get("reason") == "goal_achieved"
                else "incomplete"
            )
            summary = (
                end.get("summary", "")[:1000]
                if verified or end.get("status") == "audio_drained"
                else ""
            )
            await self.db(
                self.store.update,
                call_id,
                status="ending",
                reported_summary=summary or row["reported_summary"],
            )
            for attempt in range(self.confirmation_attempts):
                try:
                    try:
                        snapshot = await gateway.lookup(external_id)
                    except ProviderFailure:
                        snapshot = None
                    if snapshot is None or snapshot.status not in CARRIER_TERMINAL:
                        snapshot = await gateway.hangup(external_id)
                    if snapshot.status in CARRIER_TERMINAL:
                        # A failed/busy call cannot establish a successful conversation.
                        if snapshot.status != "completed" and outcome == "model_reported_success":
                            outcome = "incomplete"
                        error_code = row["error_code"]
                        if error_code == "call_end_unconfirmed":
                            error_code = None
                        # A media wait can fail because nobody answered. The carrier's
                        # confirmed disposition is more specific than that exception.
                        if not row["stop_requested"] and snapshot.status != "completed":
                            error_code = f"call_{snapshot.status}"
                        await self.db(
                            self.store.update,
                            call_id,
                            status="ended",
                            outcome=outcome,
                            error_code=error_code,
                        )
                        return
                except ProviderFailure:
                    pass
                if attempt + 1 < self.confirmation_attempts:
                    await asyncio.sleep(self.poll_seconds)
            await self.db(
                self.store.update,
                call_id,
                status="ending",
                outcome="pending",
                error_code="call_end_unconfirmed",
            )

    async def refresh(self, owner, call_id):
        call = await self.get(owner, call_id)
        if call["status"] in TERMINAL:
            return call
        if call_id not in self.tasks:
            self._spawn(call_id, self._recover_one(call_id), recovery=True)
        task = self.tasks.get(call_id)
        if task and call_id in self.recoveries:
            await asyncio.shield(task)
        return await self.get(owner, call_id)

    async def _recover_one(self, call_id):
        await self.db(LiveStore(self.store).cancel, call_id)
        row = await self.db(self.store.record, call_id)
        if row["status"] in TERMINAL:
            return
        if not row["provider_call_id"]:
            uncertain = row["dial_attempted_at"] is not None
            await self.db(
                self.store.update,
                call_id,
                status="unknown" if uncertain else "canceled",
                outcome="pending" if uncertain else "canceled",
                error_code="call_delivery_unknown" if uncertain else None,
            )
            return
        try:
            async with self.gateway_factory(self.settings_loader()) as gateway:
                await self._finish(gateway, call_id, row["provider_call_id"])
        except Exception:
            await self.db(
                self.store.update, call_id, status="ending", error_code="call_end_unconfirmed"
            )

    async def recover(self):
        for row in await self.db(self.store.active, raw=True):
            self._spawn(row["id"], self._recover_one(row["id"]), recovery=True)

    async def wait_idle(self):
        while self.submissions or self.tasks:
            await asyncio.gather(*self.submissions, *self.tasks.values(), return_exceptions=True)

    async def shutdown(self):
        self.closing = True
        if self.submissions:
            await asyncio.gather(*self.submissions, return_exceptions=True)
        for row in await self.db(self.store.active, raw=True):
            await self.stop(row["owner_id"], row["id"])
        await self.wait_idle()
