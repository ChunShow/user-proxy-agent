"""GPT-Live on the existing Azure resource. Credentials never cross origins."""

import asyncio
import json

from agent_service.calls.prompts import LIVE_INSTRUCTIONS as INSTRUCTIONS
from agent_service.calls.realtime import NoRedirectConnect, realtime_url
from agent_service.calls.types import ProviderFailure


def live_url(base):
    return realtime_url(base, "gpt-live-1").split("/realtime?")[0] + "/live/sessions"


LIVE_VOICES = frozenset({"marin", "gleam", "meridian"})



class LiveAudioSession:
    def __init__(
        self,
        base_url,
        api_key,
        model="gpt-live-1",
        *,
        voice="marin",
        connector=NoRedirectConnect,
        task="",
    ):
        self.url, self.api_key = live_url(base_url), api_key
        if model != "gpt-live-1" or len(task) > 8000 or voice not in LIVE_VOICES:
            raise ValueError("invalid_live_configuration")
        self.model, self.voice, self.task, self.connector = model, voice, task, connector

    async def __aenter__(self):
        self.context = self.connector(
            self.url,
            additional_headers={"api-key": self.api_key},
            proxy=None,
            open_timeout=12,
            close_timeout=3,
            max_size=1048576,
            max_queue=64,
        )
        self.opened = False
        try:
            async with asyncio.timeout(20):
                self.socket = await self.context.__aenter__()
                self.opened = True
                await self.socket.send(
                    json.dumps(
                        {
                            "type": "session.start",
                            "session": {
                                "model": self.model,
                                "instructions": INSTRUCTIONS + "\n요청 내용:\n" + self.task,
                                "delegation": {"type": "client"},
                                "audio": {
                                    "format": {"type": "audio/pcmu", "rate": 8000},
                                    "output": {"voice": self.voice},
                                },
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                event = json.loads(await self.socket.recv())
                session = event.get("session", {})
                if (
                    event.get("type") != "session.started"
                    or session.get("model") != self.model
                    or session.get("delegation", {}).get("type") != "client"
                    or session.get("audio", {}).get("output", {}).get("voice") != self.voice
                    or session.get("audio", {}).get("format")
                    != {"type": "audio/pcmu", "rate": 8000}
                ):
                    raise ValueError("unexpected_live_session")
            return self.socket
        except BaseException as exc:
            if self.opened:
                await self.context.__aexit__(None, None, None)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise ProviderFailure("live_setup_failed") from None

    async def __aexit__(self, *args):
        try:
            async with asyncio.timeout(5):
                await self.socket.send(json.dumps({"type": "session.close"}))
                while True:
                    event = json.loads(await self.socket.recv())
                    if event.get("type") == "session.closed":
                        break
        except Exception:
            pass  # Always release transport; call lifecycle separately confirms carrier hangup.
        finally:
            await self.context.__aexit__(*args)
