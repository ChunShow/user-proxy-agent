from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_call_store import add_user, setup_store, spec

from agent_service.calls.manager import CallManager
from agent_service.calls.store import CallStore
from agent_service.main import create_app
from agent_service.storage import StoreError


def test_mode_is_persisted_immutable_and_owner_scoped(tmp_path):
    app = create_app(database_path=tmp_path / "api.db")
    with TestClient(app) as c:
        c.post("/api/session", json={})
        cid = str(uuid4())
        body = {"conversation_id": cid, "mode": "simulation"}
        assert c.post("/api/conversations", json=body).status_code == 201
        assert c.get("/api/conversations/" + cid).json()["conversation"]["mode"] == "simulation"
        assert c.get("/api/conversations").json()["items"][0]["mode"] == "simulation"
        assert c.post("/api/conversations", json=body | {"mode": "real"}).status_code == 409
        assert c.post("/api/conversations", json=body).status_code == 201
        assert c.post("/api/conversations", json=body | {"mode": "anything"}).status_code == 422
        c.cookies.clear()
        c.post("/api/session", json={})
        assert c.get("/api/conversations/" + cid).status_code == 404


@pytest.mark.asyncio
async def test_virtual_approval_uses_saved_mode_without_real_settings(tmp_path, monkeypatch):
    db, _, owner, real_cid, _ = setup_store(tmp_path)
    cid = str(uuid4())
    db.create_conversation(owner, cid, "simulation")
    uid = add_user(db, owner, cid)["user_message_id"]
    settings = object()
    monkeypatch.setattr("agent_service.calls.manager.simulation_settings", lambda: settings)
    m = CallManager(CallStore(db), settings_loader=lambda: pytest.fail("real settings loaded"))
    call = await m.start(owner, cid, uid, spec())
    assert call["mode"] == "simulation"
    assert not m.tasks
    db.initialize()
    assert m.store.record(call["id"])["mode"] == "simulation"
    captured = []

    async def run(identity, config, call_spec):
        captured.append(config)
        m.store.update(identity, status="ended")

    monkeypatch.setattr(m, "_run", run)
    await m.approve(owner, call["id"], call["version"])
    await m.wait_idle()
    assert captured == [settings]
    assert db.get_conversation(owner, real_cid)["conversation"]["mode"] == "real"
    with pytest.raises(StoreError, match="conversation_mode_conflict"):
        db.create_conversation(owner, cid, "real")
    await m.shutdown()


@pytest.mark.asyncio
async def test_recovery_keeps_simulation_routing(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    db, _, owner, _, _ = setup_store(tmp_path)
    cid = str(uuid4())
    db.create_conversation(owner, cid, "simulation")
    uid = add_user(db, owner, cid)["user_message_id"]
    calls = CallStore(db)
    call = calls.register(owner, cid, uid, spec())
    calls.update(call["id"], status="ending", provider_call_id="SIM" + uuid4().hex)
    settings = object()
    monkeypatch.setattr("agent_service.calls.manager.simulation_settings", lambda: settings)
    gateway = object()
    captured = []

    @asynccontextmanager
    async def factory(config):
        assert config is settings
        yield gateway

    m = CallManager(
        calls,
        settings_loader=lambda: pytest.fail("real settings in recovery"),
        gateway_factory=factory,
    )

    async def finish(provider, identity, external_id):
        assert provider is gateway and external_id.startswith("SIM")
        captured.append(identity)
        calls.update(identity, status="ended")

    monkeypatch.setattr(m, "_finish", finish)
    await m.recover()
    await m.wait_idle()
    assert captured == [call["id"]]
    await m.shutdown()


@pytest.mark.asyncio
async def test_gateway_selection_never_constructs_real_carrier_for_simulation(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from agent_service.calls.manager import open_gateway

    selected = object()

    @asynccontextmanager
    async def simulated(settings):
        assert settings.carrier is None
        yield selected

    monkeypatch.setattr("agent_service.simulator.gateway.open_simulator", simulated)
    monkeypatch.setattr("agent_service.calls.manager.Gateway", lambda *args: pytest.fail("ClawOps"))
    async with open_gateway(SimpleNamespace(carrier=None)) as gateway:
        assert gateway is selected


@pytest.mark.asyncio
async def test_unavailable_simulator_releases_approved_call_slot(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from agent_service.calls.types import ProviderFailure

    db, _, owner, _, _ = setup_store(tmp_path)
    cid = str(uuid4())
    db.create_conversation(owner, cid, "simulation")
    uid = add_user(db, owner, cid)["user_message_id"]
    monkeypatch.setattr("agent_service.calls.manager.simulation_settings", lambda: object())

    @asynccontextmanager
    async def unavailable(settings):
        raise ProviderFailure("simulator_unavailable")
        yield  # pragma: no cover

    m = CallManager(CallStore(db), gateway_factory=unavailable)
    call = await m.start(owner, cid, uid, spec())
    await m.approve(owner, call["id"], call["version"])
    await m.wait_idle()
    result = await m.get(owner, call["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "call_setup_failed"
    assert m.store.active(owner) == []
    await m.shutdown()
