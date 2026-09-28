"""Separate product instance, database and browser identity, exclusively for virtual calls."""

from pathlib import Path

from agent_service.calls.manager import CallManager
from agent_service.calls.settings import CallSettings
from agent_service.main import create_app
from agent_service.settings import ROOT
from agent_service.simulator.gateway import SimulatorConfig, open_simulator
from agent_service.storage import StoreError


def create_debug_app(*, database_path=None):
    SimulatorConfig.load()  # Fail startup rather than constructing a real carrier.

    def calls(store):
        return CallManager(
            store,
            settings_loader=lambda: CallSettings.load(simulation=True),
            gateway_factory=open_simulator,
        )

    app = create_app(
        database_path=Path(database_path or ROOT / "var/simulator/debug.sqlite3"),
        call_manager_factory=calls,
        session_cookie="proxy_simulator_session",
    )

    def no_google():
        raise StoreError("google_not_configured")

    async def no_google_http(*args, **kwargs):
        raise StoreError("google_not_configured")

    app.state.integrations.settings_loader = no_google
    app.state.integrations.http = no_google_http
    app.state.calls.integrations = None
    app.state.calls.actions = None

    @app.get("/api/simulation")
    async def simulation_identity():
        return {"mode": "simulation", "real_calls": False, "number": "01000000001"}

    return app
