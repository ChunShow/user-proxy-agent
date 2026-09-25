"""Run local servers together; terminate only process groups created here."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isascii() or not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ValueError(f"{name} must be an integer between 1024 and 65535")
    return int(raw)


def check_port(number: int) -> None:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", number))
        except OSError as error:
            raise ValueError(f"Port {number} is already in use or unavailable") from error


def stop(children: list[tuple[str, subprocess.Popen]]) -> None:
    # Each child starts a new session. Never discover or kill a process by port.
    for _, child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for _, child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def main() -> int:
    children: list[tuple[str, subprocess.Popen]] = []
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        backend_port, web_port = port("BACKEND_PORT", 9010), port("WEB_PORT", 5180)
        if backend_port == web_port:
            raise ValueError("BACKEND_PORT and WEB_PORT must be different")
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js missing. Install Node 24 LTS")
        major = int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"]))
        if not 24 <= major < 27:
            raise ValueError("Node.js 24–26 required; Node 24 LTS recommended")
        vite = ROOT / "web/node_modules/vite/bin/vite.js"
        if not vite.is_file():
            raise ValueError("Web dependencies missing. Run: cd web && npm ci")
        for number in (backend_port, web_port):
            check_port(number)
        environment = {**os.environ, "BACKEND_PORT": str(backend_port), "WEB_PORT": str(web_port)}
        commands = [
            ("backend", [sys.executable, "-m", "uvicorn", "agent_service.main:app",
                         "--host", "127.0.0.1", "--port", str(backend_port)], ROOT / "backend"),
            ("web", [node, str(vite)], ROOT / "web"),
        ]
        for name, command, directory in commands:
            if stopping:
                return 0
            child = subprocess.Popen(
                command, cwd=directory, env=environment, start_new_session=True,
            )
            children.append((name, child))
            print(f"{name} pid={child.pid}", flush=True)
        print(f"Web: http://127.0.0.1:{web_port} · Ctrl-C stops both servers", flush=True)
        while not stopping:
            for name, child in children:
                if child.poll() is not None:
                    print(
                        f"{name} exited ({child.returncode}); stopping both servers",
                        file=sys.stderr,
                    )
                    return 1
            time.sleep(0.1)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Cannot start development servers: {error}", file=sys.stderr)
        return 1
    finally:
        stop(children)


if __name__ == "__main__":
    sys.exit(main())
