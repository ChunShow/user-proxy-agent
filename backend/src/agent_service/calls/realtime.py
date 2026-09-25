"""One persistent Azure speech-to-speech session for one telephone call."""

import asyncio
import json
from urllib.parse import urlencode, urlsplit

from websockets.asyncio.client import connect

from agent_service.calls.types import ProviderFailure
from agent_service.calls.voice_tools import (
    DTMF_INSTRUCTIONS,
    DTMF_TOOL,
    END_CALL_INSTRUCTIONS,
    END_CALL_TOOL,
)


def realtime_url(base_url, model):
    u = urlsplit(base_url)
    if (
        u.scheme != "https"
        or not u.hostname
        or not u.hostname.endswith(".openai.azure.com")
        or u.username
        or u.password
        or u.port not in (None, 443)
        or u.query
        or u.fragment
        or u.path.rstrip("/") not in ("", "/openai/v1")
        or not model
    ):
        raise ValueError("Expected an Azure OpenAI HTTPS resource URL and deployment name")
    return f"wss://{u.netloc}/openai/v1/realtime?" + urlencode({"model": model})


class NoRedirectConnect(connect):
    def process_redirect(self, exc):
        return ProviderFailure("azure_realtime_redirect_rejected")


INSTRUCTIONS = (
    "당신은 한국어로 통화하는 AI 도우미입니다. AI임을 숨기지 마세요. "
    "상대방이 말한 내용을 듣고 짧고 자연스러운 존댓말 한두 문장으로 응답하세요. "
    "불명확하면 짧게 되물으세요. 실제 수행하지 않은 검색·예약·결제를 했다고 말하지 마세요. "
    "음질이나 지연을 측정했다고 주장하지 마세요."
)


class AzureAudioSession:
    def __init__(self, base_url, api_key, model, *, connector=NoRedirectConnect, task=""):
        self.url = realtime_url(base_url, model)
        self.api_key, self.connector = api_key, connector
        if not isinstance(task, str) or len(task) > 8000:
            raise ValueError("Call task must be at most 8000 characters")
        self.instructions = " ".join((INSTRUCTIONS, END_CALL_INSTRUCTIONS, DTMF_INSTRUCTIONS))
        if task.strip():
            self.instructions += "\n이번 통화의 요청자가 지정한 목적:\n" + task.strip()

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
                created = json.loads(await self.socket.recv())
                if created.get("type") != "session.created":
                    raise ValueError
                await self.socket.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "realtime",
                                "instructions": self.instructions,
                                "output_modalities": ["audio"],
                                "max_output_tokens": 500,
                                "tools": [END_CALL_TOOL, DTMF_TOOL],
                                "tool_choice": "auto",
                                "audio": {
                                    "input": {
                                        "format": {"type": "audio/pcmu"},
                                        "transcription": None,
                                        "turn_detection": {
                                            "type": "server_vad",
                                            "threshold": 0.5,
                                            "prefix_padding_ms": 300,
                                            "silence_duration_ms": 600,
                                            "create_response": True,
                                            "interrupt_response": True,
                                        },
                                    },
                                    "output": {"format": {"type": "audio/pcmu"}, "voice": "alloy"},
                                },
                            },
                        }
                    )
                )
                event = json.loads(await self.socket.recv())
                if event.get("type") != "session.updated":
                    raise ValueError
                session = event["session"]
                if (
                    session["output_modalities"] != ["audio"]
                    or session["audio"]["input"]["format"]["type"] != "audio/pcmu"
                    or session["audio"]["output"]["format"]["type"] != "audio/pcmu"
                    or session["audio"]["input"].get("transcription") is not None
                ):
                    raise ValueError
            return self.socket
        except BaseException as exc:
            if self.opened:
                await self.context.__aexit__(None, None, None)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise ProviderFailure("azure_audio_setup_failed") from None

    async def __aexit__(self, *args):
        return await self.context.__aexit__(*args)
