"""Actual chat API + DeepAgents graph, with only the model transport simulated."""

import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from test_call_routes import seed
from test_native_audio import until

from agent_service.calls.delegation import DelegationCoordinator
from agent_service.calls.tools import CallContext, build_call_tools, call_history
from agent_service.chat import routes, runtime
from agent_service.main import create_app
from agent_service.settings import Settings


def test_chat_instruction_binding_dedup_ownership_and_plain_chat(monkeypatch, tmp_path):
    requests, sent = [], []
    target = {}

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        latest = body["messages"][-1]
        if latest["role"] == "user" and latest["content"] != "안녕":
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": i,
                        "id": f"update{i}",
                        "type": "function",
                        "function": {
                            "name": "update_phone_call",
                            "arguments": json.dumps(
                                {"call_id": target["id"], "instruction": "오후 6시로 확인해 주세요"}
                            ),
                        },
                    }
                    for i in range(2)
                ],
            }
            reason = "tool_calls"
        else:
            delta, reason = {"role": "assistant", "content": "처리 상태를 확인했습니다."}, "stop"
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": d, "finish_reason": r}],
            }
            for d, r in [(delta, None), ({}, reason)]
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    model_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        runtime,
        "ChatOpenAI",
        lambda **kwargs: ChatOpenAI(**(kwargs | {"http_async_client": model_client})),
    )
    monkeypatch.setattr(
        routes,
        "load_settings",
        lambda: Settings("https://model.test/v1", SecretStr("fake"), "test-chat"),
    )
    app = create_app(database_path=tmp_path / "db.sqlite3")

    class Bridge:
        ending = None

        async def command(self, *args):
            sent.append(args)

    with TestClient(app) as client:
        cid, call = seed(app, client)
        target["id"] = call["id"]
        manager = app.state.calls
        manager.store.update(call["id"], status="connected")
        coordinator = DelegationCoordinator(manager.store, call["id"], Bridge())
        manager.live_sessions[call["id"]] = coordinator
        owner = app.state.store.owner_for_token(client.cookies.get("proxy_session"))

        def chat(conversation, content):
            response = client.post(
                "/api/chat",
                json={
                    "request_id": str(uuid4()),
                    "conversation_id": conversation,
                    "content": content,
                },
            )
            assert response.status_code == 200
            assert "event: done" in response.text

        chat(cid, "안녕")
        assert not sent
        chat(cid, "오후 6시로 확인해 주세요")
        client.portal.call(until, lambda: len(sent) == 2)
        client.portal.call(
            until,
            lambda: (
                manager.store.get(owner, call["id"])["instructions"][0]["status"] == "delivered"
            ),
        )
        saved = client.get(f"/api/calls/{call['id']}").json()["instructions"]
        assert len(saved) == 1 and saved[0]["text"] == "오후 6시로 확인해 주세요"
        messages = client.get(f"/api/conversations/{cid}").json()["messages"]
        source = next(m for m in messages if m["id"] == saved[0]["source_user_message_id"])
        assert source["role"] == "user" and source["text"] == saved[0]["text"]
        schema = next(
            t for t in requests[1]["tools"] if t["function"]["name"] == "update_phone_call"
        )
        assert set(schema["function"]["parameters"]["properties"]) == {"call_id", "instruction"}
        tool_results = [
            json.loads(m["content"]) for m in requests[-1]["messages"] if m["role"] == "tool"
        ]
        assert len(tool_results) == 2 and all(
            t["instruction_id"] == saved[0]["id"] for t in tool_results
        )
        context = CallContext(manager, owner, cid, source["id"])
        history = client.portal.call(call_history, context)
        assert "delivered" in history and saved[0]["text"] in history

        other = str(uuid4())
        client.post("/api/conversations", json={"conversation_id": other})
        chat(other, "다른 대화에서 변경")
        assert all(
            "error" in json.loads(m["content"])
            for m in requests[-1]["messages"]
            if m["role"] == "tool"
        )
        manager.store.update(call["id"], status="ended")
        chat(cid, "종료 뒤 변경")
        assert all(
            "error" in json.loads(m["content"])
            for m in requests[-1]["messages"]
            if m["role"] == "tool"
        )
        wrong = build_call_tools(CallContext(manager, "other-owner", cid, source["id"]))[-1]
        assert "error" in client.portal.call(
            wrong.ainvoke, {"call_id": call["id"], "instruction": "추가"}
        )
        assert len(sent) == 2
        assert manager.store.record(call["id"])["dial_attempted_at"] is None
        client.portal.call(coordinator.close)
        client.portal.call(model_client.aclose)
