#!/usr/bin/env python3
"""Explicit operator check. --preflight never dials; --run submits through chat API."""

import argparse
import asyncio
import json
import os
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from agent_service.calls.connection import AgentConnection
from agent_service.calls.live import LiveAudioSession
from agent_service.calls.manager import open_gateway
from agent_service.calls.preflight import probe_audio
from agent_service.calls.realtime import AzureAudioSession
from agent_service.calls.settings import CallSettings, normalize_number
from agent_service.settings import ROOT


async def preflight():
    settings = CallSettings.load()
    async with open_gateway(settings) as gateway:
        await gateway.preflight()
        session = (
            LiveAudioSession(
                settings.realtime_base_url,
                settings.realtime_api_key,
                settings.live_model,
                voice=settings.live_voice,
            )
            if settings.audio_mode == "live"
            else AzureAudioSession(
                settings.realtime_base_url, settings.realtime_api_key, settings.realtime_model
            )
        )
        async with session as audio:
            size = None if settings.audio_mode == "live" else await probe_audio(audio)
            async with AgentConnection(settings.carrier):
                pass
    return {
        "carrier_access": True,
        "sender_owned_and_idle": True,
        "audio_bytes": size,
        "audio_mode": settings.audio_mode,
        "audio_generation_tested": settings.audio_mode != "live",
        "control_socket_connected": True,
        "phone_call_placed": False,
    }


async def run(to, request_id):
    # Persist the browser-equivalent identity before HTTP submission so repeating the
    # same explicit command cannot acquire a different deduplication owner.
    destination, rid = normalize_number(to), str(UUID(request_id))
    directory = ROOT / "var/live-call-check"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f"{rid}.json"
    cid = str(uuid5(NAMESPACE_URL, "agent-service/live-call/" + rid))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:9010", trust_env=False, timeout=140
    ) as client:
        if path.exists():
            client.cookies.set("proxy_session", json.loads(path.read_text())["session"])
        else:
            response = await client.post("/api/session", json={})
            response.raise_for_status()
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as file:
                json.dump({"session": client.cookies.get("proxy_session")}, file)
        response = await client.post("/api/conversations", json={"conversation_id": cid})
        response.raise_for_status()
        response = await client.post(
            "/api/chat",
            json={
                "request_id": rid,
                "conversation_id": cid,
                "content": f'{destination}로 전화해줘. 시작 멘트는 "이준수님이 요청하신 '
                "통화 기능 테스트를 "
                '진행하는 AI 도우미입니다." 목적은 지금 통화 테스트가 가능한지 물어보고 '
                "답을 재확인한 뒤 통화를 마치는 거야.",
            },
        )
        # Never print model text, telephone content, session values or URLs.
        return {
            "http_status": response.status_code,
            "conversation_id": cid,
            "request_id": rid,
            "submitted_through_chat": True,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--to")
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if args.run and (not args.to or not args.request_id):
        parser.error("--run requires --to and --request-id; it can place a billable call")
    if args.preflight and (args.to or args.request_id):
        parser.error("--preflight does not accept dialing arguments")
    try:
        result = asyncio.run(preflight() if args.preflight else run(args.to, args.request_id))
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"ok": True, **result}))


if __name__ == "__main__":
    main()
