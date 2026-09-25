import asyncio
import json

import pytest
from test_call_settings import values

from agent_service.calls.settings import CallSettings


def test_live_reuses_existing_resource_and_key(tmp_path):
    from agent_service.calls.live import live_url

    env = values() | {"CALL_AUDIO_MODE": "live", "CALL_LIVE_MODEL": "gpt-live-1"}
    config = CallSettings.load(tmp_path / "missing", environ=env)
    assert config.audio_mode == "live"
    assert config.live_model == "gpt-live-1"
    assert config.realtime_api_key == "audiosecret"
    assert live_url(config.realtime_base_url) == (
        "wss://example.openai.azure.com/openai/v1/live/sessions"
    )
    for bad in ["https://evil.test", "https://example.openai.azure.com/other", "http://localhost"]:
        with pytest.raises(ValueError):
            live_url(bad)


@pytest.mark.asyncio
async def test_live_start_validates_model_and_closes_without_audio():
    from agent_service.calls.live import LiveAudioSession
    from agent_service.calls.types import ProviderFailure

    class Socket:
        def __init__(self):
            self.sent = []
            self.events = asyncio.Queue()
            self.model = "gpt-live-1"

        async def send(self, raw):
            event = json.loads(raw)
            self.sent.append(event)
            if event["type"] == "session.start":
                session = dict(event["session"], model=self.model)
                await self.events.put(json.dumps({"type": "session.started", "session": session}))
            elif event["type"] == "session.close":
                await self.events.put(json.dumps({"type": "session.closed"}))

        async def recv(self):
            return await self.events.get()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    socket = Socket()

    def connect(url, **kwargs):
        assert kwargs["additional_headers"] == {"api-key": "secret"}
        return socket

    async with LiveAudioSession("https://example.openai.azure.com", "secret", connector=connect):
        assert socket.sent[0]["session"]["delegation"] == {"type": "client"}
        assert socket.sent[0]["session"]["audio"]["format"] == {"type": "audio/pcmu", "rate": 8000}
    assert socket.sent[-1]["type"] == "session.close"
    socket.model = "wrong"
    with pytest.raises(ProviderFailure):
        async with LiveAudioSession(
            "https://example.openai.azure.com", "secret", connector=connect
        ):
            pytest.fail("wrong model accepted")
