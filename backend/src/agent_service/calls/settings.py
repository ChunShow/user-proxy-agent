"""Independent, server-only configuration for explicitly requested calls."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from agent_service.calls.carrier import DOMESTIC, ClawOpsSettings
from agent_service.calls.live import LIVE_VOICES
from agent_service.calls.realtime import realtime_url
from agent_service.calls.types import ProviderFailure
from agent_service.settings import ROOT


def normalize_number(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[+\d ()-]{1,30}", value):
        raise ValueError("invalid_phone_number")
    number = re.sub(r"[ ()-]", "", value)
    if number.startswith("+82"):
        number = "0" + number[3:]
    if not DOMESTIC.fullmatch(number):
        raise ValueError("invalid_phone_number")
    return number


@dataclass(frozen=True)
class CallSettings:
    carrier: ClawOpsSettings = field(repr=False)
    realtime_base_url: str = field(repr=False)
    realtime_api_key: str = field(repr=False)
    realtime_model: str
    allowed_numbers: tuple[str, ...] = field(repr=False)
    max_seconds: int = 180
    audio_mode: str = "realtime"
    live_model: str = "gpt-live-1"
    live_voice: str = "marin"

    @classmethod
    def load(cls, path=None, *, environ=None):
        env = os.environ if environ is None else environ
        file = Path(path or env.get("AGENT_SERVICE_ENV_FILE") or ROOT / ".env")
        try:
            values = dict(dotenv_values(file, interpolate=False)) if file.is_file() else {}
            values.update(env)
            if values.get("CALLS_ENABLED") != "1":
                raise ProviderFailure("calls_not_configured")
            carrier = ClawOpsSettings(
                *(
                    values.get(k, "")
                    for k in ("CLAWOPS_ACCOUNT_ID", "CLAWOPS_API_KEY", "CLAWOPS_FROM_NUMBER")
                )
            )
            base, key, model = (
                values.get(k, "").strip()
                for k in ("CALL_REALTIME_BASE_URL", "CALL_REALTIME_API_KEY", "CALL_REALTIME_MODEL")
            )
            realtime_url(base, model)
            allowed = tuple(
                normalize_number(v.strip())
                for v in values.get("CALL_ALLOWED_NUMBERS", "").split(",")
            )
            seconds = int(values.get("CALL_MAX_SECONDS") or "180")
            if not key or not 1 <= seconds <= 180:
                raise ValueError
            mode = values.get("CALL_AUDIO_MODE", "realtime")
            live_model = values.get("CALL_LIVE_MODEL", "gpt-live-1")
            voice = values.get("CALL_LIVE_VOICE", "marin")
            if (
                mode not in {"realtime", "live"}
                or live_model != "gpt-live-1"
                or voice not in LIVE_VOICES
            ):
                raise ValueError
            return cls(carrier, base, key, model, allowed, seconds, mode, live_model, voice)
        except (OSError, ValueError, TypeError, AttributeError):
            raise ProviderFailure("calls_not_configured") from None
