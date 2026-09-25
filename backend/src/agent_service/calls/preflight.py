"""Non-dialing checks; never persist provider keys, URLs, or audio payloads."""

import asyncio
import json
import re

from agent_service.calls.media import audio_bytes
from agent_service.calls.types import ProviderFailure


def legacy_agent_running(processes):
    for line in processes.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 2:
            continue
        executable = parts[1].rsplit("/", 1)[-1]
        if not (
            re.match(r"python(?:\d.*)?$", executable)
            or executable in {"uv", "node", "calling-agent"}
        ):
            continue
        if "/calling-agent/" in line or re.search(r"\bcalling_agent(?:\.|\s|$)", line):
            return True
    return False


async def check_local_sender():
    process = await asyncio.create_subprocess_exec(
        "/bin/ps",
        "-axo",
        "pid=,command=",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    output, _ = await process.communicate()
    if process.returncode or legacy_agent_running(output.decode(errors="replace")):
        raise ProviderFailure("clawops_local_sender_busy")


async def probe_audio(socket):
    await socket.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "tool_choice": "none",
                    "instructions": "한국어로 연결 점검입니다 한 문장만 말하세요.",
                },
            }
        )
    )
    size = 0
    async with asyncio.timeout(30):
        while True:
            event = json.loads(await socket.recv())
            if event.get("type") == "response.output_audio.delta":
                size += len(audio_bytes(event.get("delta"), limit=240000))
            elif event.get("type") == "response.done":
                if event.get("response", {}).get("status") != "completed" or not size:
                    raise ProviderFailure("audio_probe_failed")
                return size
            elif event.get("type") == "error":
                raise ProviderFailure("audio_probe_failed")
