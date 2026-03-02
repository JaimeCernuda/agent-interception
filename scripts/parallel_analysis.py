"""Spawn parallel sub-agents to analyze different parts of the codebase.

Exercises: Task tool (subagents), parallel execution, multi-model traffic.
Run with: uv run python scripts/parallel_analysis.py
"""

from __future__ import annotations

import asyncio

from _common import CLI_PATH, WORK_DIR, banner, start_session
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage


async def main() -> None:
    session_id = start_session("parallel-analysis")
    banner("Parallel Analysis — 3 subagents at once", session_id)

    async for msg in query(
        prompt=(
            "Analyze this codebase by launching THREE sub-agents IN PARALLEL:\n\n"
            "1. Agent A: Examine src/agent_interception/providers/ — summarize the "
            "parser abstraction, how each provider differs, and how detection works.\n\n"
            "2. Agent B: Examine src/agent_interception/proxy/ — explain the streaming "
            "interception design, how bytes flow from upstream to client, and how "
            "non-streaming requests are handled.\n\n"
            "3. Agent C: Examine src/agent_interception/storage/ — describe the schema, "
            "serialization approach, and query capabilities.\n\n"
            "Launch all three simultaneously. Once they finish, synthesize their "
            "findings into a one-paragraph architecture overview."
        ),
        options=ClaudeAgentOptions(
            cli_path=CLI_PATH,
            allowed_tools=["Read", "Glob", "Grep", "Task"],
            permission_mode="bypassPermissions",
            max_turns=30,
            cwd=WORK_DIR,
        ),
    ):
        if isinstance(msg, ResultMessage) and msg.result:
            print(msg.result)


if __name__ == "__main__":
    asyncio.run(main())
