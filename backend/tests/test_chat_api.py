import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AuthenticationError, RateLimitError

from agent_service.main import create_app


def payload(messages=None):
    return {
        "request_id": str(uuid4()),
        "messages": messages or [{"role": "user", "content": "안녕하세요"}],
    }


def events(response):
    return [
        (block.splitlines()[0][7:], json.loads(block.split("data: ", 1)[1]))
        for block in response.text.split("\n\n")
        if block.startswith("event: ")
    ]


def setup(monkeypatch, runner):
    from pydantic import SecretStr

    from agent_service.chat import routes
    from agent_service.settings import Settings

    monkeypatch.setattr(
        routes,
        "load_settings",
        lambda: Settings("https://model.test/v1", SecretStr("hidden-key"), "test"),
    )
    monkeypatch.setattr(routes, "stream_reply", runner)
    return TestClient(create_app())


def test_stream_contract_and_korean_newlines(monkeypatch):
    async def reply(messages, settings):
        assert messages == [{"role": "user", "content": "안녕하세요"}]
        yield "안녕\n"
        yield '"하세요"'

    request = payload()
    response = setup(monkeypatch, reply).post("/api/chat", json=request)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data = events(response)
    assert [name for name, _ in data] == ["start", "delta", "delta", "done"]
    assert "".join(d["text"] for name, d in data if name == "delta") == '안녕\n"하세요"'
    assert {d["request_id"] for _, d in data} == {request["request_id"]}
    assert len({d["message_id"] for _, d in data}) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "bad", "messages": [{"role": "user", "content": "x"}]},
        payload([{"role": "system", "content": "x"}]),
        payload([{"role": "user", "content": "   "}]),
        payload([{"role": "assistant", "content": "x"}]),
        payload([{"role": "user", "content": "x" * 12001}]),
        payload([{"role": "user", "content": "x" * 10000}] * 7),
        {**payload(), "model": "other"},
    ],
)
def test_invalid_requests_never_call_model(body):
    response = TestClient(create_app()).post("/api/chat", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "input" not in response.text


def test_large_and_malformed_body_rejected():
    client = TestClient(create_app())
    assert (
        client.post(
            "/api/chat",
            content=b"x" * (512 * 1024 + 1),
            headers={"content-type": "application/json"},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/chat", content="{bad", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )


def test_missing_settings_are_not_reported_as_model_success(monkeypatch):
    from agent_service.chat import routes
    from agent_service.settings import SettingsError

    def missing():
        raise SettingsError("do not expose this")

    monkeypatch.setattr(routes, "load_settings", missing)
    response = TestClient(create_app()).post("/api/chat", json=payload())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"
    assert "do not expose" not in response.text


@pytest.mark.parametrize(
    "error,code,retryable",
    [
        (RuntimeError("hidden-key private upstream"), "provider_unavailable", True),
        (
            AuthenticationError(
                "hidden-key",
                response=httpx.Response(401, request=httpx.Request("POST", "https://model.test")),
                body=None,
            ),
            "provider_auth",
            False,
        ),
        (
            RateLimitError(
                "hidden-key",
                response=httpx.Response(429, request=httpx.Request("POST", "https://model.test")),
                body=None,
            ),
            "rate_limited",
            True,
        ),
    ],
)
def test_partial_failures_are_terminal_and_sanitized(monkeypatch, error, code, retryable):
    async def reply(*args):
        yield "일부 응답"
        raise error

    response = setup(monkeypatch, reply).post("/api/chat", json=payload())
    data = events(response)
    assert [name for name, _ in data] == ["start", "delta", "error"]
    assert data[-1][1]["code"] == code
    assert data[-1][1]["retryable"] is retryable
    assert "hidden-key" not in response.text


def test_empty_response_is_error(monkeypatch):
    async def reply(*args):
        if False:
            yield ""

    assert events(setup(monkeypatch, reply).post("/api/chat", json=payload()))[-1][0] == "error"


def test_heartbeat_and_deadline_close_upstream(monkeypatch):
    from agent_service.chat import routes

    closed = []

    async def reply(*args):
        try:
            await asyncio.sleep(10)
            yield "too late"
        finally:
            closed.append(True)

    monkeypatch.setattr(routes, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(routes, "CHAT_TIMEOUT_SECONDS", 0.05)
    response = setup(monkeypatch, reply).post("/api/chat", json=payload())
    assert ": keep-alive" in response.text
    assert events(response)[-1][1]["code"] == "timeout"
    assert closed == [True]
