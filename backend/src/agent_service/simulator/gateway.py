"""Adapter to the private loopback ARS API. No ClawOps fallback exists."""

import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from websockets.exceptions import WebSocketException

from agent_service.calls.audio_gateway import ModelAudioGateway
from agent_service.calls.media import MediaProtocol, NativeMedia
from agent_service.calls.realtime import NoRedirectConnect
from agent_service.calls.types import CallSnapshot, DialRejected, DialUncertain, ProviderFailure
from agent_service.settings import ROOT

SIM_ID = re.compile(r"SIM[a-f0-9]{32}")


@dataclass(frozen=True)
class SimulatorConfig:
    url: str
    token: str = field(repr=False)

    def __post_init__(self):
        u = urlsplit(self.url)
        if (
            u.scheme != "http"
            or u.hostname != "127.0.0.1"
            or not u.port
            or u.username
            or u.password
            or u.path
            or u.query
            or u.fragment
            or len(self.token) < 24
        ):
            raise ValueError("Invalid private simulator configuration")

    @classmethod
    def load(cls):
        token = os.environ.get("SIMULATOR_TOKEN", "")
        if not token:
            try:
                token = (ROOT / "var/simulator/api-token").read_text().strip()
            except OSError:
                raise ProviderFailure("simulator_unavailable") from None
        return cls(
            os.environ.get("SIMULATOR_URL", "http://127.0.0.1:9020"),
            token,
        )


class SimulatorGateway(ModelAudioGateway):
    def __init__(self, audio_settings, simulator, client):
        self.config, self.simulator, self.client = audio_settings, simulator, client

    async def request(self, method, path, **kwargs):
        try:
            return await self.client.request(
                method,
                self.simulator.url + path,
                headers={"Authorization": f"Bearer {self.simulator.token}"},
                follow_redirects=False,
                timeout=10,
                **kwargs,
            )
        except httpx.HTTPError:
            raise ProviderFailure("simulator_unavailable") from None

    @staticmethod
    def snapshot(response, expected_id=None):
        try:
            response.raise_for_status()
            row = response.json()
            if (
                not SIM_ID.fullmatch(row["id"])
                or (expected_id and row["id"] != expected_id)
                or row["status"] not in {"active", "completed", "failed"}
            ):
                raise ValueError
            return CallSnapshot(row["id"], row["status"], "simulation", row["destination"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise ProviderFailure("invalid_simulator_response") from None

    async def preflight(self):
        r = await self.request("GET", "/scenarios")
        try:
            r.raise_for_status()
            assert any(x["id"] == "clinic-hours" for x in r.json()["items"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AssertionError):
            raise ProviderFailure("simulator_unavailable") from None

    async def dial(self, call_id, destination):
        try:
            r = await self.request(
                "POST",
                "/calls",
                json={
                    "request_id": call_id,
                    "destination": destination,
                },
            )
        except ProviderFailure:
            raise DialUncertain("simulation_delivery_unknown") from None
        if 400 <= r.status_code < 500:
            raise DialRejected("simulation_number_or_request_invalid")
        return self.snapshot(r)

    async def lookup(self, external_id):
        if not SIM_ID.fullmatch(external_id):
            raise ProviderFailure("invalid_simulator_call_id")
        r = await self.request("GET", f"/calls/{external_id}")
        if r.status_code == 404:
            # Simulator state is in-memory: after restart the virtual line is gone.
            return CallSnapshot(external_id, "failed", "simulation", "01000000001")
        return self.snapshot(r, external_id)

    async def hangup(self, external_id):
        if not SIM_ID.fullmatch(external_id):
            raise ProviderFailure("invalid_simulator_call_id")
        r = await self.request("POST", f"/calls/{external_id}/hangup")
        if r.status_code == 404:
            return await self.lookup(external_id)
        return self.snapshot(r, external_id)

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def media(self, call_id):
        if not SIM_ID.fullmatch(call_id):
            raise ProviderFailure("invalid_simulator_call_id")
        url = self.simulator.url.replace("http://", "ws://", 1) + f"/calls/{call_id}/media"
        try:
            async with NoRedirectConnect(
                url,
                additional_headers={"Authorization": f"Bearer {self.simulator.token}"},
                proxy=None,
                max_size=65536,
                open_timeout=10,
                close_timeout=2,
            ) as socket:
                async with NativeMedia(socket, MediaProtocol("simulation", call_id)) as stream:
                    yield stream
        except (OSError, WebSocketException, ValueError, TimeoutError):
            raise ProviderFailure("simulator_media_failed") from None


@asynccontextmanager
async def open_simulator(settings):
    config = SimulatorConfig.load()
    async with httpx.AsyncClient(trust_env=False) as client:
        yield SimulatorGateway(settings, config, client)
