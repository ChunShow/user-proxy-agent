"""Validated raw G.711 media; no PCM decoding or speech model dependencies."""

import base64
import json
from urllib.parse import urlsplit

from agent_service.calls.types import ProviderFailure


def audio_bytes(payload, *, limit=8000):
    try:
        data = base64.b64decode(payload, validate=True)
        if not data or len(data) > limit:
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise ProviderFailure("invalid_native_audio") from None


class NativeMedia:
    def __init__(self, socket, protocol):
        self.socket, self.protocol = socket, protocol
        self.stopped = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass  # The owning AgentConnection closes its socket.

    async def events(self):
        while True:
            event = json.loads(await self.socket.recv())
            kind = event.get("event")
            if kind in ("start", "stop"):
                self.protocol.feed(event)  # Check identity and G.711 format only.
                if kind == "stop":
                    self.stopped = True
            elif kind == "media":
                if not self.protocol.started:
                    raise ProviderFailure("clawops_media_before_start")
                media = event["media"]
                if media.get("track", "inbound") != "inbound":
                    continue
                audio_bytes(media["payload"])
            yield event
            if kind == "stop":
                return

    async def send(self, event):
        await self.socket.send(json.dumps(event))

    async def send_dtmf(self, digit):
        message = dtmf_message(digit)
        if not self.protocol.started or self.stopped:
            raise ProviderFailure("clawops_media_not_active")
        await self.socket.send(message)


def validate_ws_url(url):
    u = urlsplit(url)
    if (
        u.scheme != "wss"
        or not u.hostname
        or u.username
        or u.password
        or u.fragment
        or u.port not in (None, 443)
        or not (u.hostname == "api.claw-ops.com" or u.hostname.endswith(".claw-ops.com"))
    ):
        raise ProviderFailure("untrusted_clawops_websocket")
    return url


def dtmf_message(digit):
    if not isinstance(digit, str) or len(digit) != 1 or digit not in "0123456789*#":
        raise ValueError("Expected one DTMF digit")
    return json.dumps({"event": "dtmf", "dtmf": {"digit": digit}})


class MediaProtocol:
    def __init__(self, account, call_id):
        self.account, self.call_id = account, call_id
        self.started = False

    def feed(self, message):
        event = message.get("event")
        if event == "start":
            s = message.get("start", {})
            fmt = s.get("mediaFormat", {})
            if (
                self.started
                or s.get("accountId") != self.account
                or s.get("callId") != self.call_id
                or fmt.get("sampleRate") != 8000
                or fmt.get("channels") != 1
                or fmt.get("encoding") != "audio/x-mulaw"
            ):
                raise ProviderFailure("clawops_media_identity_or_format_mismatch")
            self.started = True
        elif event == "stop":
            s = message.get("stop", {})
            if s.get("callId") != self.call_id or s.get("accountId") != self.account:
                raise ProviderFailure("clawops_stop_identity_mismatch")
        return []
