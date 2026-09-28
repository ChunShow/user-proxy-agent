"""Run an isolated virtual ARS, agent backend and web. Never launches a real call provider."""

import os
import secrets
import shutil
import signal
import subprocess
import sys
import time

from dev import ROOT, check_port, stop
from prepare_virtual_ars import prepare


def main():
    children = []
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        for port in (9020, 9011, 5181):
            check_port(port)
        audio = prepare()
        directory = ROOT / "var/simulator"
        key = directory / "api-token"
        if not key.exists():
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as target:
                target.write(secrets.token_urlsafe(32))
        key.chmod(0o600)
        token = key.read_text().strip()
        if len(token) < 24:
            raise ValueError("Invalid local simulator token")
        node = shutil.which("node")
        vite = ROOT / "web/node_modules/vite/bin/vite.js"
        if not node or not vite.is_file():
            raise ValueError("Install the web dependencies before starting the simulator")
        env = {
            **os.environ,
            "SIMULATOR_TOKEN": token,
            "SIMULATOR_AUDIO_DIR": str(audio),
            "SIMULATOR_URL": "http://127.0.0.1:9020",
            "WEB_PORT": "5181",
            "BACKEND_PORT": "9011",
            "VITE_SIMULATION": "1",
            "CALLS_ENABLED": "1",
            "CALL_AUDIO_MODE": "live",
            "CALL_LIVE_MODEL": "gpt-live-1",
            "AGENT_SERVICE_DATABASE_PATH": str(directory / "debug.sqlite3"),
        }
        commands = [
            (
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
            ),
            (
                "debug-agent",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "agent_service.simulator.debug_app:create_debug_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9011",
                ],
                ROOT / "backend",
            ),
            ("debug-web", [node, str(vite)], ROOT / "web"),
        ]
        for name, command, cwd in commands:
            p = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
            children.append((name, p))
            print(f"{name} pid={p.pid}", flush=True)
        print(
            "Virtual calls only: http://127.0.0.1:5181 · ARS API: http://127.0.0.1:9020", flush=True
        )
        print("Test line: 01000000001 · Ctrl-C stops this stack only", flush=True)
        while not stopping:
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError(f"{name} stopped; shutting down the virtual stack")
            time.sleep(0.1)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Cannot start virtual ARS: {error}", file=sys.stderr)
        return 1
    finally:
        stop(children)


if __name__ == "__main__":
    sys.exit(main())
