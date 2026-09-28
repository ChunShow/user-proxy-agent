"""Opt-in real-model checks of call proposals. Carrier access is structurally blocked."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

from agent_service.calls.manager import CallManager
from agent_service.calls.store import CallSpec, CallStore
from agent_service.calls.tools import CallContext
from agent_service.chat.runtime import stream_reply
from agent_service.chat.schemas import ChatRequest
from agent_service.settings import load_settings
from agent_service.storage import ConversationStore


class ProposalOnlyManager(CallManager):
    def __init__(self, store):
        super().__init__(store, settings_loader=lambda: None, gateway_factory=self.block_gateway)
        self.start_attempts = 0
        self.gateway_attempts = 0

    def block_gateway(self, *_):
        self.gateway_attempts += 1
        raise AssertionError("carrier_access_forbidden")

    async def start(self, *args):
        self.start_attempts += 1
        return await super().start(*args)

    async def approve(self, *_):
        raise AssertionError("approval_forbidden_in_evaluation")


def save_exchange(db, owner, cid, user, assistant):
    identity = db.begin_run(
        owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content=user)
    )
    db.save_run(owner, identity, assistant, "completed")
    return identity


REQUEST = "01000000001로 전화해서 내일 오후 1시 남자 커트 예약해줘. 이름은 김테스트야."
CASES = [
    (
        "date",
        "01000000001로 남자 커트 오후 1시 예약 전화해줘. 이름은 김테스트야.",
        "어느 날짜로 예약할까요?",
        "내일",
        "new",
    ),
    (
        "name",
        "01000000001로 전화해서 내일 오후 1시 남자 커트 예약해줘.",
        "예약자 성함을 알려주세요.",
        "김테스트",
        "new",
    ),
    (
        "retry",
        REQUEST,
        "승인 카드 생성에 실패했습니다. 전화는 걸리지 않았습니다.",
        "다시 해줘",
        "new",
    ),
    (
        "short_call",
        REQUEST,
        "승인 카드 생성에 실패했습니다. 전화는 걸리지 않았습니다.",
        "01000000001로 걸어줘",
        "new",
    ),
    ("cancel", REQUEST, "예약 조건을 확인하고 있습니다.", "아니 취소해. 전화하지 마.", "none"),
    ("fees", REQUEST, "예약 조건을 확인하고 있습니다.", "통화 요금은 얼마야?", "none"),
    (
        "other_task",
        REQUEST,
        "예약 조건을 확인하고 있습니다.",
        "예약은 됐고 이제 점심 메뉴 추천해줘.",
        "none",
    ),
    (
        "pending",
        REQUEST,
        "발신 승인 카드를 만들었습니다. 카드에서 승인해 주세요.",
        "다시 해줘",
        "existing",
    ),
    (
        "cancel_pending",
        REQUEST,
        "발신 승인 카드를 만들었습니다. 카드에서 승인해 주세요.",
        "이 전화 요청 취소해줘.",
        "canceled",
    ),
    (
        "missing_number",
        "내일 오후 1시 남자 커트 예약해줘. 이름은 김테스트야.",
        "전화할 번호를 알려주세요.",
        "번호는 모르겠어. 알아서 해줘.",
        "none",
    ),
]


async def check(root, case):
    name, request, clarification, answer, expected = case
    db = ConversationStore(root / name / "db.sqlite3")
    db.initialize()
    owner = db.owner_for_token(db.issue_session())
    cid = str(uuid4())
    db.create_conversation(owner, cid)
    identity = save_exchange(db, owner, cid, request, clarification)
    store = CallStore(db)
    manager = ProposalOnlyManager(store)
    if expected in {"existing", "canceled"}:
        store.register(
            owner,
            cid,
            identity["user_message_id"],
            CallSpec(
                destination="01000000001",
                subject="남자 커트 예약",
                purpose="김테스트의 내일 오후 1시 남자 커트 예약",
                opening_message="안녕하세요, 예약을 대신 요청하는 AI 도우미입니다.",
                questions=["내일 오후 1시에 남자 커트 예약 가능한가요?"],
            ),
        )
    identity = db.begin_run(
        owner, ChatRequest(request_id=uuid4(), conversation_id=cid, content=answer)
    )
    messages = [
        {"role": "user", "content": request},
        {"role": "assistant", "content": clarification},
        {"role": "user", "content": answer},
    ]
    try:
        async with asyncio.timeout(120):
            response = "".join(
                [
                    part
                    async for part in stream_reply(
                        messages,
                        load_settings(),
                        CallContext(manager, owner, cid, identity["user_message_id"]),
                    )
                ]
            )
        rows = store.list(owner, cid)["items"]
        assert manager.gateway_attempts == 0 and not manager.tasks
        assert all(store.record(r["id"])["dial_attempted_at"] is None for r in rows)
        assert all(r["approved_at"] is None for r in rows)
        if expected in {"new", "existing"}:
            assert len(rows) == 1 and rows[0]["status"] == "awaiting_approval"
            assert rows[0]["destination"] == "01000000001"
            if expected == "new":
                assert "김테스트" in json.dumps(rows[0], ensure_ascii=False)
                assert manager.start_attempts == 1
            else:
                assert manager.start_attempts == 0
        elif expected == "canceled":
            assert len(rows) == 1 and rows[0]["status"] == "canceled"
            assert manager.start_attempts == 0
        else:
            assert not rows and manager.start_attempts == 0
        print(
            json.dumps(
                {
                    "case": name,
                    "result": "passed",
                    "start_attempts": manager.start_attempts,
                    "card_statuses": [r["status"] for r in rows],
                    "gateway_attempts": manager.gateway_attempts,
                    "synthetic_response": response,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await manager.shutdown()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Make billable model requests")
    parser.add_argument("--case", choices=[c[0] for c in CASES])
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to execute real-model evaluations")
    with tempfile.TemporaryDirectory() as directory:
        for case in CASES:
            if args.case is None or case[0] == args.case:
                await check(Path(directory), case)


if __name__ == "__main__":
    asyncio.run(main())
