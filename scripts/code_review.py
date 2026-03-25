"""Ask an agent to do a deep code review of the project.

Exercises: heavy tool use (Read, Glob, Grep), large context, streaming.
Run with: uv run python scripts/code_review.py
"""

from __future__ import annotations

import asyncio

from _common import CLI_PATH, WORK_DIR, banner, start_session
from claude_agent_sdk import ClaudeAgentOptions, query


async def main() -> None:
    session_id = start_session("code-review")
    banner("Code Review — full codebase read + analysis", session_id)

    async for msg in query(
        prompt=(
            "Do a thorough code review of this Python project. "
            "First, use Glob to discover every .py file under src/. "
            "Then read each one. For each module note: its purpose, "
            "public API surface, anything questionable. "
            "After reading everything, write up a structured review "
            "covering architecture, error handling, and test coverage gaps."
        ),
        options=ClaudeAgentOptions(
            cli_path=CLI_PATH,
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="bypassPermissions",
            max_turns=30,
            cwd=WORK_DIR,
        ),
    ):
        if hasattr(msg, "result"):
            print(msg.result)  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
