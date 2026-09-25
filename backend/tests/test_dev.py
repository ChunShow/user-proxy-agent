"""Exercise the real development launcher and its owned server processes."""

import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("Condition was not reached before timeout")


def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
            return json.load(response) == {"status": "ok", "service": "agent-service"}
    except (URLError, TimeoutError, ConnectionError):
        return False


def port_closed(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def launch(backend_port, web_port, output):
    return subprocess.Popen(
        ["bash", str(ROOT / "scripts/dev.sh")],
        cwd=ROOT,
        env={**os.environ, "BACKEND_PORT": str(backend_port), "WEB_PORT": str(web_port)},
        stdout=output,
        stderr=subprocess.STDOUT,
    )


def test_start_interrupt_restart_and_child_exit(tmp_path):
    backend_port, web_port = available_port(), available_port()
    while web_port == backend_port:
        web_port = available_port()
    # Reuse the same ports to catch leaked children after the first shutdown.
    for stop_child in (False, True):
        log = tmp_path / f"run-{stop_child}.log"
        with log.open("w") as output:
            process = launch(backend_port, web_port, output)
            try:
                wait_for(lambda: healthy(backend_port) and healthy(web_port))
                if stop_child:
                    match = re.search(r"backend pid=(\d+)", log.read_text())
                    assert match, log.read_text()
                    os.kill(int(match[1]), signal.SIGTERM)
                else:
                    process.send_signal(signal.SIGINT)
                returncode = process.wait(timeout=10)
                assert returncode == (1 if stop_child else 0), log.read_text()
                wait_for(lambda: port_closed(backend_port) and port_closed(web_port))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


def test_port_conflict_preserves_existing_listener(tmp_path):
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        occupied_port = existing.getsockname()[1]
        other_port = available_port()
        with (tmp_path / "conflict.log").open("w+") as output:
            process = launch(occupied_port, other_port, output)
            assert process.wait(timeout=10) != 0
            output.seek(0)
            assert "already in use" in output.read()
        assert not port_closed(occupied_port)
        assert port_closed(other_port)
