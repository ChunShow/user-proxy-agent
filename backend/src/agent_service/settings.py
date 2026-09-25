"""Read only this service's model configuration; never log credential values."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[3]
KEYS = ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME", "MODEL_MAX_TOKENS", "MODEL_TRUST_ENV")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str = field(repr=False)
    api_key: SecretStr = field(repr=False)
    model_name: str
    max_tokens: int = 2048
    trust_env: bool = True


def load_settings(path=None, *, environ=None) -> Settings:
    env = os.environ if environ is None else environ
    file = Path(path or env.get("AGENT_SERVICE_ENV_FILE") or ROOT / ".env")
    try:
        values = dotenv_values(file, interpolate=False) if file.is_file() else {}
        values = {key: env.get(key, values.get(key) or "") for key in KEYS}
        url, key, model = (values[k].strip() for k in KEYS[:3])
        parsed = urlsplit(url)
        if not url or not key or not model:
            raise ValueError
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"})
        ):
            raise ValueError
        _ = parsed.port
        tokens = int(values["MODEL_MAX_TOKENS"] or "2048")
        trust = values["MODEL_TRUST_ENV"] or "1"
        if tokens <= 0 or trust not in {"0", "1"}:
            raise ValueError
        return Settings(url.rstrip("/"), SecretStr(key), model, tokens, trust == "1")
    except (ValueError, OSError, TypeError):
        raise SettingsError("서버의 모델 설정을 확인해 주세요.") from None
