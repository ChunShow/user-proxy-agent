"""Lifespan-owned call work; HTTP/chat cancellation never owns the carrier call."""

import asyncio
import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from starlette.concurrency import run_in_threadpool

from agent_service.calls.audio_gateway import ModelAudioGateway
from agent_service.calls.bridge import NativeAudioBridge
from agent_service.calls.carrier import ClawOpsControl
from agent_service.calls.connection import AgentConnection
from agent_service.calls.delegation import DelegationCoordinator
from agent_service.calls.instructions import InstructionStore
from agent_service.calls.listening import AudioHub, ObservedMedia
from agent_service.calls.live_bridge import LiveBridge
from agent_service.calls.live_store import LiveStore
from agent_service.calls.preflight import check_local_sender
from agent_service.calls.settings import CallSettings, normalize_number
from agent_service.calls.store import TERMINAL, CallSpec
from agent_service.calls.types import TERMINAL as CARRIER_TERMINAL
from agent_service.calls.types import DialRejected, DialUncertain, ProviderFailure
from agent_service.storage import StoreError


class Gateway(ModelAudioGateway, ClawOpsControl):
    def __init__(self, settings, client):
        if settings.carrier is None:
            raise ProviderFailure("real_carrier_not_configured")
        super().__init__(settings.carrier, client)
        self.config = settings

    async def preflight(self):
        await check_local_sender()
        return await super().preflight()

    def connection(self):
        return AgentConnection(self.settings)


def simulation_settings():
    return CallSettings.load(
        simulation=True,
        environ={**os.environ, "CALLS_ENABLED": "1", "CALL_AUDIO_MODE": "live"},
    )


@asynccontextmanager
async def open_gateway(settings):
    if settings.carrier is None:
        from agent_service.simulator.gateway import open_simulator

        async with open_simulator(settings) as gateway:
            yield gateway
        return
    async with httpx.AsyncClient(trust_env=False) as client:
        yield Gateway(settings, client)


def call_is_prohibited(current):
    # A direct prohibition can reject a proposal; it is not a general intent parser.
    # Do not confuse constraints such as "예약은 하지 말고 시간만 물어봐" with no call.
    return bool(
        re.search(
            r"(?:전화|통화)(?:를|는|도|은)?\s*"
            r"(?:(?:절대|다시|아직|지금|오늘|내일|당장은|이제|함부로)\s*)*"
            r"(?:(?:연결|시작)?\s*하지|걸지|걸어\s*주지|해\s*주지|안\s*(?:걸|해)|말아|말고|금지)",
            current,
        )
    )


def user_provided_number(texts, destination):
    # Intent and clarification answers are resolved by the main conversation agent.
    # This only grounds a reversible proposal in user-authored numbers. Actual
    # dialing is separately authorized by the owner's versioned approval button.
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
        self.live_sessions = {}
        self.audio_hubs = {}
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

    async def update(self, owner, conversation_id, source_user_message_id, call_id, instruction):
        context = SimpleNamespace(
            owner=owner,
            conversation_id=conversation_id,
            source_user_message_id=source_user_message_id,
        )
        return await self._owned(self._update_instruction(context, call_id, instruction))

    async def _update_instruction(self, context, call_id, instruction):
        prior = await self.db(InstructionStore(self.store).prior, context, call_id, instruction)
        if prior:
            return prior
        coordinator = self.live_sessions.get(call_id)
        if self.closing or coordinator is None:
            raise StoreError("call_instruction_unavailable")
        return await coordinator.submit(context, instruction)

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

    def settings_for(self, mode):
        if mode == "simulation":
            return simulation_settings()
        if mode != "real":
            raise ProviderFailure("invalid_call_mode")
        return self.settings_loader()

    async def conversation_mode(self, owner, cid):
        result = await self.db(self.store.db.get_conversation, owner, cid)
        return result["conversation"]["mode"]

    async def _register(self, owner, cid, uid, spec):
        mode = await self.conversation_mode(owner, cid)
        self.settings_for(mode)
        current, texts = await self.db(self.store.user_texts, owner, cid, uid)
        if call_is_prohibited(current):
            raise StoreError("call_request_canceled", 422)
        if not user_provided_number(texts, spec.destination):
            raise StoreError("call_number_required", 422)
        return await self.db(self.store.register, owner, cid, uid, spec)

    async def approve(self, owner, call_id, expected_version):
        if self.closing:
            raise StoreError("calls_unavailable", 503)
        return await self._owned(self._approve(owner, call_id, expected_version))

    async def _approve(self, owner, call_id, expected_version):
        call = await self.get(owner, call_id)
        try:
            settings = self.settings_for(call["mode"])
        except ProviderFailure:
            raise StoreError("calls_not_configured", 503) from None
        call, claimed = await self.db(self.store.approve, owner, call_id, expected_version)
        if claimed:
            row = await self.db(self.store.record, call_id)
            spec = CallSpec.model_validate_json(row["spec"])
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
                row = await self.db(self.store.record, call_id)
                integrations = (
                    None if row["mode"] == "simulation" else getattr(self, "integrations", None)
                )
                bridge.coordinator = DelegationCoordinator(
                    self.store, call_id, bridge, integrations=integrations
                )
                self.live_sessions[call_id] = bridge.coordinator
            try:
                hub = AudioHub()
                self.audio_hubs[call_id] = hub
                return await bridge.run(ObservedMedia(media, hub))
            finally:
                hub = self.audio_hubs.pop(call_id, None)
                if hub:
                    hub.close()
                self.live_sessions.pop(call_id, None)
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
                await self.audit(call_id, "call_connection_exit", {"trigger": "time_limit"})
                await self.db(self.store.update, call_id, error_code="call_time_limit")
            if tasks[0] in done:
                await self.audit(call_id, "call_connection_exit", {"trigger": "media"})
                return tasks[0].result()
            if tasks[2] in done:
                await self.audit(call_id, "call_connection_exit", {"trigger": "carrier"})
                tasks[2].result()
            elif tasks[1] in done:
                await self.audit(call_id, "call_connection_exit", {"trigger": "user_stop"})
            return {}
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, call_id, settings, spec):
        try:
            await self._run_with_gateway(call_id, settings, spec)
        except (ProviderFailure, ValueError):
            row = await self.db(self.store.record, call_id)
            if row["dial_attempted_at"] is None and row["status"] not in TERMINAL:
                await self.db(
                    self.store.update, call_id, status="failed", outcome="incomplete",
                    error_code="call_setup_failed",
                )
            else:
                raise

    async def _run_with_gateway(self, call_id, settings, spec):
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

    async def audit(self, call_id, kind, content):
        try:
            await self.db(LiveStore(self.store).event, call_id, kind, content)
        except Exception:
            pass

    async def _finish(self, gateway, call_id, external_id, report=None):
        await self.db(InstructionStore(self.store).abort, call_id, "call_ended")
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
                if verified
                or end.get("status") == "audio_drained"
                or (
                    (report or {}).get("mode") == "gpt_live"
                    and end.get("status") == "playback_unconfirmed"
                )
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
                        trigger = (
                            "user_stop"
                            if row["stop_requested"]
                            else "playback_ready"
                            if end.get("status") in {"played", "audio_drained"}
                            else "cleanup"
                        )
                        await self.audit(
                            call_id,
                            "carrier_hangup_requested",
                            {
                                "trigger": trigger,
                                "attempt": attempt + 1,
                            },
                        )
                        end["carrier_action"] = "hangup_requested"
                        await self.db(self.store.update, call_id, end_report=json.dumps(end))
                        started = time.monotonic()
                        try:
                            snapshot = await gateway.hangup(external_id)
                        except ProviderFailure:
                            await self.audit(
                                call_id,
                                "carrier_hangup_failed",
                                {
                                    "attempt": attempt + 1,
                                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                                },
                            )
                            raise
                        await self.audit(
                            call_id,
                            "carrier_hangup_returned",
                            {
                                "status": snapshot.status,
                                "elapsed_ms": round((time.monotonic() - started) * 1000),
                            },
                        )
                    if snapshot.status in CARRIER_TERMINAL:
                        if end.get("carrier_action") != "hangup_requested":
                            end["carrier_action"] = "already_ended"
                            await self.audit(
                                call_id, "carrier_already_ended", {"status": snapshot.status}
                            )
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
                            end_report=json.dumps(end),
                        )
                        await self.audit(
                            call_id, "carrier_end_confirmed", {"status": snapshot.status}
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
            await self.audit(call_id, "carrier_end_unconfirmed", {})

    async def refresh(self, owner, call_id):
        call = await self.get(owner, call_id)
        if call["status"] in TERMINAL or call["status"] == "awaiting_approval":
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
                error_code="call_delivery_unknown"
                if uncertain
                else ("call_approval_canceled" if row["status"] == "awaiting_approval" else None),
            )
            return
        try:
            async with self.gateway_factory(self.settings_for(row["mode"])) as gateway:
                await self._finish(gateway, call_id, row["provider_call_id"])
        except Exception:
            await self.db(
                self.store.update, call_id, status="ending", error_code="call_end_unconfirmed"
            )

    async def recover(self):
        await self.db(InstructionStore(self.store).recover)
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
