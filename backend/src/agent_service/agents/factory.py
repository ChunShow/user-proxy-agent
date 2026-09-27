"""Construct a tool-limited DeepAgents graph with an explicit caller policy."""

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain.agents.middleware import TodoListMiddleware
from langchain_openai import ChatOpenAI


def create_tool_agent(model: ChatOpenAI, *, tools=None, system_prompt: str):
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
        model=model,
        tools=tools or [],
        system_prompt=system_prompt,
        middleware=[TodoListMiddleware()],
    )
