"""Prepare the standalone ARS child used by the ordinary development launcher."""

import os
import secrets
import sys

from dev import ROOT
from prepare_virtual_ars import prepare


def configuration():
    audio = prepare()
    key = ROOT / "var/simulator/api-token"
    if not key.exists():
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(secrets.token_urlsafe(32))
    key.chmod(0o600)
    token = key.read_text().strip()
    if len(token) < 24:
        raise ValueError("Invalid local simulator token")
    environment = {
        "SIMULATOR_TOKEN": token,
        "SIMULATOR_AUDIO_DIR": str(audio),
        "SIMULATOR_URL": "http://127.0.0.1:9020",
    }
    command = (
        "virtual-ars",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agent_service.simulator.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "9020",
        ],
        ROOT / "backend",
    )
    return environment, command
