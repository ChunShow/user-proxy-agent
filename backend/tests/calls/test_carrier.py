import json

import httpx
import pytest

from agent_service.calls.types import DialRejected, DialUncertain, ProviderFailure


def settings():
    from agent_service.calls.carrier import ClawOpsSettings

    return ClawOpsSettings("ACtest", "sk_test_secret", "07011112222")


def call_body(status="queued"):
    return {
        "callId": "CAabc123",
        "accountId": "ACtest",
        "from": "07011112222",
        "to": "15885700",
        "direction": "outbound",
        "status": status,
    }


@pytest.mark.asyncio
async def test_domestic_representative_number_and_bearer_auth_are_sent_once():
    from agent_service.calls.carrier import ClawOpsControl

    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer sk_test_secret"
        assert json.loads(request.content) == {
            "To": "15885700",
            "From": "07011112222",
            "Timeout": 25,
        }
        return httpx.Response(201, json=call_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await ClawOpsControl(settings(), client).dial("local", "15885700")
    assert result.external_id == "CAabc123"
    assert result.status == "queued"
    assert len(requests) == 1


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(201, json={})])
@pytest.mark.asyncio
async def test_ambiguous_dial_is_never_retried(response):
    from agent_service.calls.carrier import ClawOpsControl

    requests = []

    def handle(request):
        requests.append(request)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(DialUncertain):
            await ClawOpsControl(settings(), client).dial("local", "15885700")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_foreign_or_e164_representative_number_never_reaches_carrier():
    from agent_service.calls.carrier import ClawOpsControl

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("sent"))) as c:
        for number in ("+8215885700", "+12025550123", "15885700/evil"):
            with pytest.raises(DialRejected):
                await ClawOpsControl(settings(), c).dial("local", number)


@pytest.mark.asyncio
async def test_hangup_ack_is_followed_by_lookup_and_identity_validation():
    from agent_service.calls.carrier import ClawOpsControl

    requests = []

    def handle(r):
        requests.append(r.method)
        if r.method == "POST":
            assert json.loads(r.content) == {"Status": "completed"}
            return httpx.Response(200, json={"callId": "CAabc123", "status": "completed"})
        return httpx.Response(200, json=call_body("completed"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        result = await ClawOpsControl(settings(), c).hangup("CAabc123")
    assert result.status == "completed"
    assert requests == ["POST", "GET"]


@pytest.mark.parametrize(
    "url",
    ["wss://evil.test/media", "ws://api.claw-ops.com/a", "wss://api.claw-ops.com.evil.test/a"],
)
def test_media_url_cannot_exfiltrate_key(url):
    from agent_service.calls.media import validate_ws_url

    with pytest.raises(ProviderFailure):
        validate_ws_url(url)


@pytest.mark.asyncio
async def test_media_wrong_call_or_codec_is_rejected():
    from agent_service.calls.media import MediaProtocol

    for account, sid, rate in [
        ("ACother", "CAabc123", 8000),
        ("ACtest", "CAwrong", 8000),
        ("ACtest", "CAabc123", 16000),
    ]:
        p = MediaProtocol("ACtest", "CAabc123")
        with pytest.raises(ProviderFailure):
            p.feed(
                {
                    "event": "start",
                    "start": {
                        "accountId": account,
                        "callId": sid,
                        "mediaFormat": {
                            "encoding": "audio/x-mulaw",
                            "sampleRate": rate,
                            "channels": 1,
                        },
                    },
                }
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [True, False])
async def test_preflight_checks_all_active_states_before_socket_takeover(busy):
    from agent_service.calls.carrier import ClawOpsControl

    seen = []

    def handle(r):
        seen.append(r)
        if r.url.path.endswith("/numbers"):
            return httpx.Response(200, json={"data": [{"number": "07011112222"}]})
        assert r.method == "GET" and r.url.params["number"] == "07011112222"
        return httpx.Response(200, json={"data": [call_body("in-progress")] if busy else []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        carrier = ClawOpsControl(settings(), c)
        if busy:
            with pytest.raises(ProviderFailure, match="sender_busy"):
                await carrier.preflight()
        else:
            await carrier.preflight()
            assert {r.url.params.get("status") for r in seen[1:]} == {
                "queued",
                "ringing",
                "in-progress",
            }
