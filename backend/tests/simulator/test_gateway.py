from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_service.calls.settings import CallSettings
from agent_service.calls.types import DialRejected
from agent_service.simulator.debug_app import create_debug_app
from agent_service.simulator.gateway import SimulatorConfig, SimulatorGateway


def test_simulation_needs_no_clawops_and_has_separate_cookie(tmp_path, monkeypatch):
    from agent_service.calls.manager import Gateway

    monkeypatch.setattr(Gateway, "__init__", lambda *a: pytest.fail("real carrier instantiated"))
    monkeypatch.setenv("SIMULATOR_TOKEN", "local-token-not-clawops-12345")
    app = create_debug_app(database_path=tmp_path / "debug.db")
    with TestClient(app) as c:
        assert c.post("/api/session", json={}).status_code == 204
        assert c.cookies.get("proxy_simulator_session")
        assert not c.cookies.get("proxy_session")
        assert c.get("/api/integrations/google").json()["status"] == "not_configured"
        assert c.get("/api/calls/active").json()["items"] == []
    values = {
        "CALLS_ENABLED": "1",
        "CALL_REALTIME_BASE_URL": "https://example.openai.azure.com",
        "CALL_REALTIME_API_KEY": "test-audio-key",
        "CALL_REALTIME_MODEL": "deployment",
    }
    cfg = CallSettings.load(tmp_path / "missing", environ=values, simulation=True)
    assert cfg.carrier is None


@pytest.mark.asyncio
async def test_gateway_calls_only_local_service_and_never_falls_back():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(422, json={"detail": "simulation_number_unknown"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        g = SimulatorGateway(None, SimulatorConfig("http://127.0.0.1:9020", "t" * 32), client)
        with pytest.raises(DialRejected):
            await g.dial(str(uuid4()), "01000000002")
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].headers["authorization"] == "Bearer " + "t" * 32


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.test:9020",
        "http://localhost.evil.test:9020",
        "http://127.0.0.1:9020/extra",
        "https://127.0.0.1:9020",
    ],
)
def test_simulator_url_cannot_send_credentials_outside_loopback(url):
    with pytest.raises(ValueError):
        SimulatorConfig(url, "t" * 32)
