"""DeepAgents boundary: expose text, close provider connections on cancellation."""

from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from agent_service.actions.tools import action_history, build_action_tools
from agent_service.agents.factory import create_tool_agent
from agent_service.calls.tools import CallContext, build_call_tools, call_history
from agent_service.chat.prompts import SYSTEM_PROMPT
from agent_service.integrations.tools import build_integration_tools
from agent_service.observability import trace_execution
from agent_service.settings import Settings

# Graph steps include middleware as well as model/tool work. Leave room for
# search plus several detail reads while retaining a finite loop guard.
AGENT_RECURSION_LIMIT = 64


def build_agent(model: ChatOpenAI, *, call_tools=None, system_prompt=SYSTEM_PROMPT):
    return create_tool_agent(model, tools=call_tools, system_prompt=system_prompt)


def visible_text(chunk) -> str:
    if not isinstance(chunk, AIMessageChunk) or chunk.tool_call_chunks:
        return ""
    if isinstance(chunk.content, str):
        return chunk.content
    return "".join(
        block.get("text", "")
        for block in chunk.content
        if isinstance(block, dict) and block.get("type") == "text"
    )


async def stream_agent(agent, messages: list[dict], *, callbacks=None) -> AsyncIterator[str]:
    stream = agent.astream(
        {"messages": messages},
        stream_mode="messages",
        config={"recursion_limit": AGENT_RECURSION_LIMIT, "callbacks": callbacks or []},
        subgraphs=False,
    )
    try:
        async for chunk, _metadata in stream:
            text = visible_text(chunk)
            if text:
                yield text
    finally:
        await stream.aclose()


async def stream_reply(
    messages: list[dict], settings: Settings, call_context: CallContext | None = None
) -> AsyncIterator[str]:
    call_tools = build_call_tools(call_context) if call_context else []
    if call_context and getattr(call_context.manager, "integrations", None):
        call_tools += build_integration_tools(call_context.manager.integrations, call_context.owner)
    if call_context and getattr(call_context.manager, "actions", None):
        actions = call_context.manager.actions
        call_tools += build_action_tools(actions, call_context)
        records = await action_history(actions, call_context)
        messages = [*messages[:-1], {"role": "user", "content": records}, messages[-1]]
    if call_context:
        records = await call_history(call_context)
        if records:
            messages = [*messages[:-1], {"role": "user", "content": records}, messages[-1]]
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
        prompt = (
            SYSTEM_PROMPT + " 현재 한국 시간: " + datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
        )
        identity = (
            (call_context.conversation_id, call_context.source_user_message_id, None)
            if call_context
            else None
        )
        with trace_execution(
            "main-chat", settings.model_name, [t.name for t in call_tools], identity=identity
        ) as callbacks:
            stream = stream_agent(
                build_agent(model, call_tools=call_tools, system_prompt=prompt),
                messages,
                callbacks=callbacks,
            )
            try:
                async for text in stream:
                    yield text
            finally:
                await stream.aclose()
