import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from agent_service.settings import ROOT
from agent_service.storage import StoreError

CALENDAR_READ = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_LIST = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
EMAIL = "https://www.googleapis.com/auth/userinfo.email"
READ_SCOPES = (EMAIL, CALENDAR_READ, CALENDAR_LIST, GMAIL_READ)
CALENDAR_WRITE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
WRITE_SCOPES = (*READ_SCOPES, CALENDAR_WRITE, GMAIL_SEND)


@dataclass(frozen=True)
class GoogleSettings:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def load(cls):
        path = Path(os.environ.get("AGENT_SERVICE_ENV_FILE") or ROOT / ".env")
        values = dict(dotenv_values(path, interpolate=False)) if path.is_file() else {}
        values.update(os.environ)
        client, secret = (
            values.get(k, "").strip() for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
        )
        port = os.environ.get("WEB_PORT", "5180")
        default = f"http://127.0.0.1:{port}/api/integrations/google/callback"
        redirect = values.get("GOOGLE_REDIRECT_URI", "").strip() or default
        url = urlsplit(redirect)
        if not client or not secret or redirect != default or url.query or url.fragment:
            raise StoreError("integration_not_configured")
        return cls(client, secret, redirect)
