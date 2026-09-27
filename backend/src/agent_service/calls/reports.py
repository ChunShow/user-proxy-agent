"""Background, tool-free summaries. Call teardown never waits on this worker."""

import asyncio
import json
import logging

import httpx
from langchain_openai import ChatOpenAI

from agent_service.calls.live_store import LiveStore
from agent_service.calls.prompts import REPORT_PROMPT as PROMPT
from agent_service.calls.report_store import CallReportStore, fallback, report_text
from agent_service.observability import trace_execution
from agent_service.settings import load_settings

log = logging.getLogger(__name__)


async def generate_report(context):
    settings = load_settings()
    async with httpx.AsyncClient(trust_env=settings.trust_env, timeout=30) as client:
        model = ChatOpenAI(
            model=settings.model_name,
            base_url=settings.base_url,
            api_key=settings.api_key,
            max_tokens=min(settings.max_tokens, 1200),
            max_retries=0,
            timeout=30,
            http_async_client=client,
        )
        with trace_execution(
            "call-result", settings.model_name, [], identity=context["identity"]
        ) as callbacks:
            result = await model.ainvoke(
                [
                    {"role": "system", "content": PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {k: v for k, v in context.items() if k != "identity"},
                            ensure_ascii=False,
                        ),
                    },
                ],
                config={"callbacks": callbacks},
            )
            # Responses-compatible endpoints may return reasoning and text blocks.
            text = result.content if isinstance(result.content, str) else "".join(
                block["text"] for block in result.content
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
            if result.tool_calls or not text.strip():
                raise ValueError("invalid_call_report")
            if len(text) > 3800:
                raise ValueError("oversized_call_report")
            return text.strip()


class CallReportManager:
    def __init__(self, calls, *, generator=generate_report, timeout=30, poll_seconds=0.5):
        self.calls, self.store = calls, CallReportStore(calls)
        self.generator, self.timeout, self.poll_seconds = generator, timeout, poll_seconds
        self.task = None

    async def db(self, method, *args):
        # A cancelled worker must let its SQLite transaction settle before recovery.
        task = asyncio.create_task(asyncio.to_thread(method, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def start(self):
        if self.task and not self.task.done():
            return
        await self.db(self.store.recover)
        self.task = asyncio.create_task(self._work())

    async def _work(self):
        while True:
            try:
                # A previous publication error must not regenerate the model result.
                await self.db(self.store.recover)
                if await self.run_once():
                    continue
            except Exception:
                log.warning("Call result processing unavailable; retaining durable job")
            await asyncio.sleep(self.poll_seconds)

    async def run_once(self):
        call = await self.db(self.store.claim)
        if not call:
            return False
        error = None
        try:
            transcript = await self.db(
                LiveStore(self.calls).transcript_evidence, call["owner_id"], call["id"]
            )
            spec = json.loads(call["spec"])
            context = {
                "identity": (call["conversation_id"], call["source_user_message_id"], call["id"]),
                "call": {
                    k: call[k]
                    for k in (
                        "status",
                        "outcome",
                        "reported_summary",
                        "end_report",
                        "stop_requested",
                        "error_code",
                    )
                },
                "task": {k: spec[k] for k in ("subject", "purpose", "questions")},
                "summary_timing": "end_request",
                "transcript": transcript,
            }
            async with asyncio.timeout(self.timeout):
                text = await self.generator(context)
            if not isinstance(text, str) or not text.strip() or len(text) > 3800:
                raise ValueError("invalid_call_report")
        except asyncio.CancelledError:
            raise
        except Exception:
            text, error = fallback(call), "report_unavailable"
        await self.db(self.store.complete, call["id"], report_text(call, text), error)
        return True

    async def shutdown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.db(self.store.recover)
