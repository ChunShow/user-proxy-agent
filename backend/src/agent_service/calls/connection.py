"""Outbound-only reverse WebSockets; no public tunnel or inbound-call handler."""

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from agent_service.calls.media import MediaProtocol, NativeMedia, validate_ws_url
from agent_service.calls.types import ProviderFailure


class TrustedConnect(connect):
    def process_redirect(self, exc):
        target = super().process_redirect(exc)
        if isinstance(target, str):
            try:
                validate_ws_url(target)
            except ProviderFailure as error:
                return error
        return target


class AgentConnection:
    def __init__(self, settings, *, connector=TrustedConnect):
        self.settings, self.connector = settings, connector

    def connect(self, url):
        validate_ws_url(url)
        return self.connector(
            url,
            additional_headers={"Authorization": f"Bearer {self.settings.api_key}"},
            proxy=None,
            open_timeout=12,
            close_timeout=3,
            max_size=65536,
            max_queue=32,
            ping_interval=20,
            ping_timeout=20,
        )

    async def __aenter__(self):
        url = (
            f"wss://api.claw-ops.com/v1/accounts/{self.settings.account}/agent/listen?"
            + urlencode({"number": self.settings.from_number})
        )
        self.context = self.connect(url)
        try:
            self.socket = await self.context.__aenter__()
        except (OSError, WebSocketException, TimeoutError):
            raise ProviderFailure("clawops_control_connect_failed") from None
        return self

    async def __aexit__(self, *args):
        return await self.context.__aexit__(*args)

    @asynccontextmanager
    async def media(self, call_id, *, transport_factory=None):
        try:
            async with asyncio.timeout(40):
                async for raw in self.socket:
                    event = json.loads(raw)
                    if event.get("event") == "agent.retired":
                        raise ProviderFailure("clawops_number_taken_over")
                    if event.get("callId") != call_id:
                        continue
                    if event.get("event") in ("call.failed", "call.ended"):
                        raise ProviderFailure("clawops_call_not_connected")
                    if event.get("event") == "call.outbound_ready":
                        url = event["mediaUrl"]
                        # Production advertises ws://api... behind its TLS proxy.
                        # Upgrade only this known origin; never send the key in cleartext.
                        parsed = urlsplit(url)
                        if parsed.scheme == "ws" and parsed.netloc == "api.claw-ops.com":
                            url = parsed._replace(scheme="wss").geturl()
                        url = validate_ws_url(url)
                        break
                else:
                    raise ProviderFailure("clawops_control_closed")
            async with self.connect(url) as socket:
                async with (transport_factory or NativeMedia)(
                    socket, MediaProtocol(self.settings.account, call_id)
                ) as t:
                    yield t
        except (OSError, WebSocketException, ValueError, KeyError, TypeError):
            raise ProviderFailure("clawops_stream_failed") from None
