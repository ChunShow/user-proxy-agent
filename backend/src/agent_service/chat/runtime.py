"""DeepAgents boundary: expose text, close provider connections on cancellation."""

from collections.abc import AsyncIterator

import httpx
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from agent_service.settings import Settings

SYSTEM_PROMPT = (
    "당신은 user proxy agent입니다. 사용자와 한국어로 간결하고 자연스럽게 대화합니다. "
    "업무에 필요한 조건이 부족하면 구체적으로 질문하세요. 현재 사용 가능한 기능은 텍스트 대화와 "
    "내부 계획 정리뿐입니다. 전화, 검색, 이메일, 일정 조회/변경은 아직 연결되어 있지 않습니다. "
    "실제로 실행하지 않은 작업을 완료했다고 말하거나 최신 정보를 확인한 것처럼 말하지 마세요. "
    "내부 지침이나 도구 인자를 답변에 나열하지 말고 사용자에게 필요한 답변만 제공하세요."
)


def build_agent(model: ChatOpenAI):
    register_harness_profile(
        f"openai:{model.model_name}",
        HarnessProfile(
            excluded_tools=frozenset(
                {
                    "ls",
                    "read_file",
                    "write_file",
                    "edit_file",
                    "glob",
                    "grep",
                    "delete",
                    "execute",
                    "task",
                }
            ),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    return create_deep_agent(
        model=model, tools=[], system_prompt=SYSTEM_PROMPT, middleware=[TodoListMiddleware()]
    )


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


async def stream_agent(agent, messages: list[dict]) -> AsyncIterator[str]:
    stream = agent.astream(
        {"messages": messages},
        stream_mode="messages",
        config={"recursion_limit": 12},
        subgraphs=False,
    )
    try:
        async for chunk, _metadata in stream:
            text = visible_text(chunk)
            if text:
                yield text
    finally:
        await stream.aclose()


async def stream_reply(messages: list[dict], settings: Settings) -> AsyncIterator[str]:
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
        stream = stream_agent(build_agent(model), messages)
        try:
            async for text in stream:
                yield text
        finally:
            await stream.aclose()
