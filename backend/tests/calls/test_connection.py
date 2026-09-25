import json
from contextlib import asynccontextmanager

import pytest
from test_carrier import settings
from test_native_audio import Socket, phone_start

from agent_service.calls.connection import AgentConnection
from agent_service.calls.types import ProviderFailure


class Control(Socket):
    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()


@pytest.mark.asyncio
async def test_reverse_socket_accepts_only_own_call_and_known_tls_media():
    control, media = Control(), Socket()
    await control.input.put(
        {"event": "call.outbound_ready", "callId": "CAother", "mediaUrl": "ws://evil.test"}
    )
    await control.input.put(
        {
            "event": "call.outbound_ready",
            "callId": "CAtest",
            "mediaUrl": "ws://api.claw-ops.com/media",
        }
    )
    await media.input.put(phone_start())
    urls = []

    @asynccontextmanager
    async def connect(url, **options):
        urls.append(url)
        assert options["additional_headers"]["Authorization"] == "Bearer sk_test_secret"
        yield control if len(urls) == 1 else media

    async with AgentConnection(settings(), connector=connect) as conn:
        async with conn.media("CAtest") as stream:
            event = await anext(stream.events())
            assert event["event"] == "start"
            await stream.send_dtmf("4")
            assert media.sent[-1] == {"event": "dtmf", "dtmf": {"digit": "4"}}
    assert urls[-1] == "wss://api.claw-ops.com/media"


@pytest.mark.asyncio
async def test_retired_control_socket_is_failure():
    c = Control()
    await c.input.put({"event": "agent.retired"})
    async with AgentConnection(settings(), connector=c.connect) as conn:
        with pytest.raises(ProviderFailure, match="taken_over"):
            async with conn.media("CAtest"):
                pytest.fail("connected")


@pytest.mark.asyncio
async def test_configured_opening_replaces_generic_greeting():
    import asyncio

    from test_native_audio import until

    from agent_service.calls.bridge import NativeAudioBridge
    from agent_service.calls.media import MediaProtocol, NativeMedia

    model, phone = Socket(), Socket()
    bridge = NativeAudioBridge(model, opening_message="지정된 시작 멘트입니다.")
    task = asyncio.create_task(bridge.run(NativeMedia(phone, MediaProtocol("ACtest", "CAtest"))))
    await phone.input.put(phone_start())
    await until(lambda: model.sent)
    assert "지정된 시작 멘트입니다." in json.dumps(model.sent, ensure_ascii=False)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
