"""Opt-in, metadata-only Langfuse tracing. Never pass prompts or tool payloads."""

import hashlib
import json
import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage

from agent_service.settings import ROOT

log = logging.getLogger(__name__)
_client = None
_identity = ContextVar("trace_identity", default=None)


@dataclass(frozen=True)
class TraceSettings:
    base_url: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)


def load_trace_settings(path=None, *, environ=None):
    env = os.environ if environ is None else environ
    try:
        file = Path(path or env.get("AGENT_SERVICE_ENV_FILE") or ROOT / ".env")
        values = dotenv_values(file, interpolate=False) if file.is_file() else {}
        get = lambda key: env.get(key, values.get(key) or "")  # noqa: E731
        if get("LANGFUSE_TRACING_ENABLED") != "1":
            return None
        url = get("LANGFUSE_BASE_URL").rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
            or not get("LANGFUSE_PUBLIC_KEY")
            or not get("LANGFUSE_SECRET_KEY")
        ):
            return None
        _ = parsed.port
        return TraceSettings(url, get("LANGFUSE_PUBLIC_KEY"), get("LANGFUSE_SECRET_KEY"))
    except (ValueError, OSError, TypeError):
        return None


def trace_id(identity):
    return hashlib.sha256(("agent-service:" + identity).encode()).hexdigest()[:32]


def get_trace_client():
    global _client
    settings = load_trace_settings()
    if settings is None:
        return None
    if _client is None:
        try:
            import httpx
            from langfuse import Langfuse
            from opentelemetry.sdk.trace import TracerProvider

            _client = Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key,
                base_url=settings.base_url,
                environment="local",
                timeout=2,
                flush_interval=1,
                httpx_client=httpx.Client(timeout=2, trust_env=False),
                # Do not export unrelated global OpenTelemetry instrumentation.
                tracer_provider=TracerProvider(),
                debug=False,
            )
        except Exception:
            log.warning("Local tracing initialization failed; continuing without tracing")
    return _client


def shutdown_tracing():
    global _client
    client, _client = _client, None
    if client:
        try:
            client.shutdown()
        except Exception:
            log.warning("Local tracing shutdown failed")


@contextmanager
def correlation(conversation_id, source_message_id, call_id=None):
    token = _identity.set((conversation_id, source_message_id, call_id))
    try:
        yield
    finally:
        _identity.reset(token)


def result_failed(output):
    if isinstance(output, ToolMessage):
        if output.status == "error":
            return True
        output = output.content
    if isinstance(output, str) and len(output) <= 100_000:
        try:
            output = json.loads(output)
        except (ValueError, TypeError):
            return False
    return isinstance(output, dict) and bool(output.get("error"))


class SafeCallback(AsyncCallbackHandler):
    # LangChain awaits these inexpensive in-memory SDK operations. Export is batched
    # on the SDK worker thread; no HTTP request occurs in the audio/model loop.
    run_inline = True
    raise_error = False

    def __init__(self, root, model_name, tool_names):
        self.root, self.runs = root, {}
        self.model_name = model_name if re.fullmatch(r"[\w.:-]{1,100}", model_name) else "model"
        self.tool_names = frozenset(tool_names)
        self.closed = False

    def start(self, run_id, name, kind):
        if self.closed or run_id in self.runs:
            return
        try:
            options = {"model": self.model_name} if kind == "generation" else {}
            self.runs[run_id] = self.root.start_observation(
                name=name, as_type=kind, metadata={"status": "running"}, **options
            )
        except Exception:
            pass

    @staticmethod
    def finish_span(span, status):
        try:
            span.update(
                metadata={"status": status},
                level="ERROR"
                if status == "error"
                else "WARNING"
                if status == "canceled"
                else "DEFAULT",
                status_message=status,
            )
        except Exception:
            pass
        finally:
            try:
                span.end()
            except Exception:
                pass

    def finish(self, run_id, status):
        span = self.runs.pop(run_id, None)
        if span is not None:
            self.finish_span(span, status)

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.start(run_id, self.model_name, "generation")

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.start(run_id, self.model_name, "generation")

    async def on_llm_end(self, response, *, run_id, **kwargs):
        self.finish(run_id, "completed")

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.finish(run_id, "error" if isinstance(error, Exception) else "canceled")

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = (serialized or {}).get("name")
        self.start(run_id, name if name in self.tool_names else "tool", "tool")

    async def on_tool_end(self, output, *, run_id, **kwargs):
        self.finish(run_id, "error" if result_failed(output) else "completed")

    async def on_tool_error(self, error, *, run_id, **kwargs):
        self.finish(run_id, "error" if isinstance(error, Exception) else "canceled")

    def close(self, status):
        self.closed = True
        for run_id in list(self.runs):
            self.finish(run_id, "canceled" if status == "completed" else status)
        self.finish_span(self.root, status)


@contextmanager
def trace_execution(kind, model_name, tool_names, *, identity=None):
    """One root per execution, sharing a trace across the originating user request."""
    callback = None
    try:
        client = get_trace_client()
        if client:
            from langfuse import propagate_attributes

            conversation, source, call = identity or _identity.get() or (None, None, None)
            trace = trace_id(source or str(uuid4()))
            # Fixed names and hashed opaque IDs only; no account or contact details.
            metadata = {"privacy": "metadata-only"}
            if call:
                metadata["call_ref"] = trace_id(call)
            with propagate_attributes(
                session_id=trace_id(conversation) if conversation else None,
                trace_name="agent-request",
            ):
                root = client.start_observation(
                    name=kind, as_type="agent", trace_context={"trace_id": trace}, metadata=metadata
                )
            callback = SafeCallback(root, model_name, {*tool_names, "write_todos"})
    except Exception:
        log.warning("Local tracing unavailable; continuing execution")
    status = "canceled"
    try:
        yield [callback] if callback else []
        status = "completed"
    except Exception:
        status = "error"
        raise
    finally:
        if callback:
            callback.close(status)


CALL_EVENTS = frozenset(
    {
        "end_tool_requested",
        "end_final_answer_ready",
        "end_decision_canceled",
        "end_decision_discarded",
        "end_requested",
        "end_canceled",
        "farewell_commands_started",
        "farewell_commands_acked",
        "farewell_commands_failed",
        "end_playback_finished",
        "completion_review_finished",
        "completion_review_discarded",
        "call_connection_exit",
        "carrier_hangup_requested",
        "carrier_hangup_returned",
        "carrier_hangup_failed",
        "carrier_end_confirmed",
        "carrier_end_unconfirmed",
    }
)
_EVENT_ENUMS = {
    "reason": {
        "goal_achieved",
        "recipient_declined",
        "unable_to_continue",
        "caller_resumed",
        "caller_audio_resumed",
        "output_cleared",
        "context_changed",
        "task_canceled",
    },
    "status": {
        "audio_drained",
        "playback_unconfirmed",
        "active",
        "completed",
        "failed",
        "busy",
        "no_answer",
        "canceled",
        "ringing",
        "queued",
        "unknown",
    },
    "trigger": {"media", "user_stop", "carrier", "time_limit", "playback_ready", "cleanup"},
}
_EVENT_NUMBERS = frozenset(
    {
        "wait_ms",
        "voice_end_bytes",
        "acked_bytes",
        "pending_voice_packets",
        "queued_packets",
        "buffer_bytes",
        "output_quiet_ms",
        "input_quiet_ms",
        "elapsed_ms",
        "attempt",
    }
)


def record_call_event(row, kind, content):
    """Export an allowlisted milestone; never arbitrary SQLite event content."""
    if kind not in CALL_EVENTS:
        return
    try:
        client = get_trace_client()
        if client is None:
            return
        from langfuse import propagate_attributes

        metadata = {"call_ref": trace_id(row["id"])}
        for key, allowed in _EVENT_ENUMS.items():
            value = content.get(key)
            if isinstance(value, str) and value in allowed:
                metadata[key] = value
        for key in _EVENT_NUMBERS:
            value = content.get(key)
            if type(value) in (int, float) and 0 <= value <= 10**12:
                metadata[key] = value
        if type(content.get("end_requested")) is bool:
            metadata["end_requested"] = content["end_requested"]
        with propagate_attributes(
            session_id=trace_id(row["conversation_id"]), trace_name="agent-request"
        ):
            span = client.start_observation(
                name="call." + kind,
                as_type="span",
                trace_context={"trace_id": trace_id(row["source_user_message_id"])},
                metadata=metadata,
                level="WARNING"
                if kind.endswith(("failed", "unconfirmed", "canceled"))
                else "DEFAULT",
            )
        span.end()
    except Exception:
        pass  # Carrier control must not depend on telemetry availability.
