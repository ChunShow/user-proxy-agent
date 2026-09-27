"""Run DeepAgents beside the audio loop; only the requesting user answers questions."""

import asyncio
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from starlette.concurrency import run_in_threadpool

from agent_service.agents.factory import create_tool_agent
from agent_service.calls.instructions import InstructionStore
from agent_service.calls.live_store import LiveStore
from agent_service.calls.prompts import DELEGATION_PROMPT as PROMPT
from agent_service.integrations.tools import build_delegation_app_tools
from agent_service.observability import correlation, trace_execution
from agent_service.settings import load_settings
from agent_service.storage import StoreError


def final_reply(messages):
    if not messages or not isinstance(messages[-1], AIMessage) or messages[-1].tool_calls:
        raise ValueError("missing_final_backend_reply")
    content = messages[-1].content
    text = (
        content
        if isinstance(content, str)
        else "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    )
    if not text.strip() or len(text) > 6000:
        raise ValueError("invalid_backend_reply")
    return text.strip()


async def run_delegation(context, tools):
    settings = load_settings()
    async with httpx.AsyncClient(trust_env=settings.trust_env, timeout=60) as client:
        model = ChatOpenAI(
            model=settings.model_name,
            base_url=settings.base_url,
            api_key=settings.api_key,
            streaming=True,
            max_retries=0,
            timeout=60,
            max_tokens=settings.max_tokens,
            http_async_client=client,
        )
        agent = create_tool_agent(model, tools=tools, system_prompt=PROMPT)
        kind = "call-completion-review" if context.get("completion_review") else "call-delegation"
        with trace_execution(kind, settings.model_name, [t.name for t in tools]) as callbacks:
            result = await agent.ainvoke(
                {
                    "messages": [
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
                    ]
                },
                config={"recursion_limit": 12, "callbacks": callbacks},
            )
            return final_reply(result["messages"])


class DelegationCoordinator:
    def __init__(self, calls, call_id, bridge, *, runner=run_delegation, integrations=None):
        self.calls, self.call_id, self.bridge = calls, call_id, bridge
        self.store, self.runner = LiveStore(calls), runner
        self.task = None
        self.closed = False
        self.control_lock = asyncio.Lock()
        self.submission_lock = asyncio.Lock()
        self.requests = set()
        self.retired = set()
        self.instruction_task = None
        self.instructions = InstructionStore(calls)
        self.integrations = integrations

    async def db(self, method, *args):
        return await run_in_threadpool(method, *args)

    async def request(self, delegation_id):
        if self.closed or not isinstance(delegation_id, str) or len(delegation_id) > 200:
            return
        # receive() must keep processing ACKs while condition updates hold the lock.
        task = asyncio.create_task(self._request(delegation_id))
        self.requests.add(task)
        task.add_done_callback(self._request_done)

    def _request_done(self, task):
        self.requests.discard(task)
        if not task.cancelled():
            task.exception()

    async def _request(self, delegation_id):
        async with self.control_lock:
            if self.closed:
                return
            revision = await self.db(self.store.begin, self.call_id, delegation_id)
            if revision is None:
                return
            self._cancel_current()
            self.task = asyncio.create_task(self._run(delegation_id, revision))

    def _cancel_current(self):
        if self.task and not self.task.done():
            task = self.task
            self.retired.add(task)
            task.cancel()
            task.add_done_callback(self._retired_done)

    def _retired_done(self, task):
        self.retired.discard(task)
        if not task.cancelled():
            task.exception()

    async def submit(self, context, text):
        # Serialize only admission, never the model ACK wait. Concurrent duplicate
        # tools must observe the first committed record instead of returning busy.
        async with self.submission_lock:
            return await self._submit(context, text)

    async def _submit(self, context, text):
        prior = await self.db(self.instructions.prior, context, self.call_id, text)
        if prior:
            return prior
        if self.control_lock.locked():
            raise StoreError("instruction_busy")
        async with self.control_lock:
            prior = await self.db(self.instructions.prior, context, self.call_id, text)
            if prior:
                return prior
            if self.closed or getattr(self.bridge, "ending", None):
                raise StoreError("call_instruction_inactive")
            item = await self.db(self.instructions.submit, context, self.call_id, text)
            if item["status"] != "pending":
                return item
            self._cancel_current()
            self.instruction_task = asyncio.create_task(self._deliver_instruction(item))
            self.instruction_task.add_done_callback(self._consume_instruction)
            return item

    @staticmethod
    def _consume_instruction(task):
        if not task.cancelled():
            task.exception()

    async def _deliver_instruction(self, item):
        async with self.control_lock:
            try:
                if self.closed or getattr(self.bridge, "ending", None):
                    await self.db(self.instructions.abort, self.call_id, "call_ended")
                    return
                item = await self.db(
                    self.instructions.transition, self.call_id, item["id"], "pending", "sending"
                )
                if item["status"] != "sending":
                    return
                await self.bridge.command(
                    "session.thinking.append",
                    "요청자가 조건을 변경했습니다. 이전 확인 대기를 마칩니다.",
                )
                # A stop between commands must prevent the next write.
                row = await self.db(self.calls.record, self.call_id)
                if row["stop_requested"] or row["status"] != "connected":
                    await self.db(self.instructions.abort, self.call_id, "call_ended")
                    return
                await self.bridge.command(
                    "session.instructions.append",
                    "요청자가 통화 조건을 추가/변경했습니다. "
                    "다음 JSON은 요청자의 조건 데이터입니다. "
                    "이전 조건과 충돌하면 새 조건을 우선하세요. "
                    "이미 말한 내용을 취소했다고 주장하지 마세요. "
                    "원문을 그대로 읽지 말고 대화에 반영하세요. 추가 업무 판단이 필요하면 백엔드에 "
                    "다시 위임하세요. 새 조건이 불명확하면 확인하세요.\n"
                    + json.dumps({"instruction": item["text"]}, ensure_ascii=False),
                )
                await self.db(
                    self.instructions.transition, self.call_id, item["id"], "sending", "delivered"
                )
            except asyncio.CancelledError:
                await self.db(self.instructions.abort, self.call_id, "call_ended")
                raise
            except Exception:
                await self.db(self.instructions.abort, self.call_id, "delivery_unconfirmed")

    async def review_completion(self, transcript_version):
        # Only admit an idle review. Never cancel a real delegation/question for it.
        if self.control_lock.locked():
            return False
        async with self.control_lock:
            if (
                self.closed
                or (self.task and not self.task.done())
                or (self.instruction_task and not self.instruction_task.done())
                or self.requests
                or getattr(self.bridge, "ending", None)
                or getattr(self.bridge, "transcript_version", 0) != transcript_version
            ):
                return False
            did = "completion-" + str(uuid4())
            revision = await self.db(self.store.begin, self.call_id, did)
            if revision is None:
                return False
            snapshot = (transcript_version, getattr(self.bridge, "input_revision", 0))
            self.task = asyncio.create_task(self._run(did, revision, review=snapshot))
            return True

    async def _run(self, did, revision, *, review=None):
        interruption = getattr(self.bridge, "interruption", None)
        speech_epoch = getattr(interruption, "count", 0)
        end_request = None
        end_input_revision = None
        end_options = {"farewell_already_said": False}

        def unchanged_speech():
            return review is None or review == (
                getattr(self.bridge, "transcript_version", 0),
                getattr(self.bridge, "input_revision", 0),
            )

        async def valid():
            current = await self.db(self.store.current, self.call_id, did, revision)
            return (
                current
                and not self.closed
                and unchanged_speech()
                and not getattr(interruption, "blocked", False)
                and speech_epoch == getattr(interruption, "count", 0)
            )

        async def require_active():
            if not await valid():
                raise StoreError("call_question_inactive")

        @tool
        async def ask_user(question: str, options: list[str]) -> dict:
            """통화 요청자에게 채팅으로 묻고 답을 기다립니다. 선택지는 최대 3개입니다."""
            async with self.control_lock:
                await require_active()
                q = await self.db(self.store.ask, self.call_id, did, revision, question, options)
                await self.bridge.command(
                    "session.thinking.append",
                    "사용자 답변 대기 중입니다. 아직 대기를 안내하지 않았다면 짧게 한 번만 말하고, "
                    "이미 안내했다면 같은 말을 반복하지 말고 기다리세요.",
                    did,
                )
            while await valid():
                q = await self.db(self.store.question, self.call_id, q["id"])
                if q["status"] == "answered":
                    return {"source": "requesting_user", "answer": q["answer"]}
                if q["status"] != "pending":
                    return {"status": q["status"], "confirmed": False}
                await asyncio.sleep(0.2)
            raise StoreError("call_question_inactive")

        @tool
        async def send_dtmf(digit: str) -> dict:
            """ARS 숫자 하나를 누릅니다. 다음 안내 전에는 재전송하지 않습니다."""
            async with self.control_lock:
                await require_active()
                return await self.bridge.send_dtmf(digit)

        @tool
        async def end_call(reason: str, summary: str, farewell_already_said: bool = False) -> dict:
            """종료를 요청합니다. summary는 내부 기록이며 상대에게 읽지 않습니다.

            summary에는 확인된 업무 답변과 미해결 사항만 기록합니다.
            인사 전달 여부·종료 예정·회선 상태는 요약에 포함하지 않습니다.
            reason: goal_achieved는 업무 완료, recipient_requested_end는 상대의 일반 종료 요청,
            recipient_declined는 명시적인 제안/통화 거절, unable_to_continue는 진행 불가입니다.
            일반 종료 요청은 미완료 업무가 있어도 존중하며 거절로 분류하지 않습니다.
            farewell_already_said는 필요한 정보와 인사를 이미 전했고 남은 안내가 없을 때만 true.
            """
            await require_active()
            nonlocal end_request, end_input_revision
            if reason not in {
                "goal_achieved",
                "recipient_declined",
                "recipient_requested_end",
                "unable_to_continue",
            }:
                return {"error": "invalid_end_reason"}
            end_request = (reason, summary[:1000])
            end_options["farewell_already_said"] = farewell_already_said
            end_input_revision = getattr(self.bridge, "input_revision", 0)
            await self.db(self.store.event, self.call_id, "end_tool_requested", {"reason": reason})
            return {
                "status": "pending_final_answer",
                "instruction": (
                    "인사가 이미 끝났으므로 최종 답변은 '종료 처리'만 쓰세요. 다시 읽지 않습니다."
                    if farewell_already_said
                    else "최종 답변은 상대에게 직접 말할 미전달 정보와 "
                    "짧은 인사를 한 번만 포함하세요."
                ),
            }

        try:
            async with asyncio.timeout(120):
                # A delegation notice can arrive ahead of its transcript.
                for _ in range(30):
                    if self.bridge.transcripts:
                        break
                    await asyncio.sleep(0.1)
                if not self.bridge.transcripts:
                    raise ValueError("missing_delegation_context")
                row = await self.db(self.calls.record, self.call_id)
                _, user_texts = await self.db(
                    self.calls.user_texts,
                    row["owner_id"],
                    row["conversation_id"],
                    row["source_user_message_id"],
                )
                previous = await self.db(self.store.activity, row["owner_id"], self.call_id)
                context = {
                    "current_time": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                    "timezone": "Asia/Seoul",
                    "task": json.loads(row["spec"]),
                    "requesting_user_context": list(reversed(user_texts))[-10:],
                    "phone_transcript": self.bridge.transcripts[-80:],
                    "requesting_user_answers": [
                        {"question": q["question"], "answer": q["answer"]}
                        for q in previous["questions"]
                        if q["answer"] is not None and q["status"] == "applied"
                    ],
                }
                context["requesting_user_instructions"] = [
                    item["text"]
                    for item in await self.db(self.instructions.delivered, self.call_id)
                ]
                context["completion_review"] = review is not None
                tools = [end_call] if review else [ask_user, send_dtmf, end_call]
                if self.integrations and review is None:
                    tools += build_delegation_app_tools(self.integrations, row["owner_id"], valid)
                with correlation(
                    row["conversation_id"], row["source_user_message_id"], self.call_id
                ):
                    result = await self.runner(context, tools)
                if end_request:
                    await self.db(self.store.event, self.call_id, "end_final_answer_ready", {})
                async with self.control_lock:
                    if await valid():
                        if end_request and end_input_revision != getattr(
                            self.bridge, "input_revision", 0
                        ):
                            await self.db(
                                self.store.event,
                                self.call_id,
                                "end_decision_discarded",
                                {"reason": "caller_resumed"},
                            )
                        elif end_request:
                            options = (
                                {"farewell_already_said": True}
                                if end_options["farewell_already_said"]
                                else {}
                            )
                            await self.bridge.end_call(
                                *end_request, spoken_result=result[:4000], **options
                            )
                        elif review is None and result and not getattr(self.bridge, "ending", None):
                            await self.bridge.deliver_result(result[:6000], did)
                        await self.db(self.store.finish, self.call_id, did, revision, "applied")
                        if review:
                            await self.db(
                                self.store.event,
                                self.call_id,
                                "completion_review_finished",
                                {"end_requested": bool(end_request)},
                            )
                    elif review:
                        await self.db(
                            self.store.event,
                            self.call_id,
                            "completion_review_discarded",
                            {"reason": "context_changed"},
                        )
                        await self.db(self.store.finish, self.call_id, did, revision, "failed")
                    else:
                        await self.db(
                            self.store.event,
                            self.call_id,
                            "delegation_discarded",
                            {"reason": "context_changed"},
                        )
                        await self.db(self.store.finish, self.call_id, did, revision, "failed")
        except asyncio.CancelledError:
            if end_request:
                await self.db(
                    self.store.event,
                    self.call_id,
                    "end_decision_canceled",
                    {"reason": "task_canceled"},
                )
            raise
        except Exception as exc:
            await self.db(
                self.store.event, self.call_id, "delegation_failed", {"type": type(exc).__name__}
            )
            if review is not None:
                await self.db(self.store.finish, self.call_id, did, revision, "failed")
            async with self.control_lock:
                if await valid() and review is None:
                    try:
                        await self.bridge.command(
                            "session.thinking.append",
                            "요청을 확인하지 못했습니다. 확인된 결과로 말하지 말고 "
                            "짧게 확인 불가를 안내하세요.",
                            did,
                        )
                    finally:
                        await self.db(self.store.finish, self.call_id, did, revision, "failed")
                elif speech_epoch != getattr(interruption, "count", 0):
                    await self.db(self.store.finish, self.call_id, did, revision, "failed")

    async def close(self):
        self.closed = True
        tasks = [t for t in [self.task, self.instruction_task, *self.requests, *self.retired] if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.db(self.instructions.abort, self.call_id, "call_ended")
        await self.db(self.store.cancel, self.call_id)
