"""Multi-turn conversation: iteratively refactor a module.

Exercises: multi-turn with session resume, Read + Write + Bash,
extended thinking on design decisions, context accumulation.
Run with: uv run python scripts/multi_turn_refactor.py
"""

from __future__ import annotations

import asyncio
import dataclasses

from _common import CLI_PATH, WORK_DIR, banner, start_session
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage


async def main() -> None:
    session_id = start_session("multi-turn-refactor")
    banner("Multi-Turn Refactor — 3 rounds with session resume", session_id)

    sdk_session_id: str | None = None
    base_opts = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        allowed_tools=["Read", "Glob", "Grep", "Write", "Bash"],
        permission_mode="bypassPermissions",
        max_turns=15,
        cwd=WORK_DIR,
    )

    # --- Turn 1: Read and assess ---
    print("[Turn 1] Reading and assessing the display module...")
    async for msg in query(
        prompt=(
            "Read src/agent_interception/display/terminal.py carefully. "
            "Identify every potential improvement: code style, missing edge "
            "cases, robustness issues, missing features. List them clearly "
            "but do NOT make changes yet."
        ),
        options=base_opts,
    ):
        if isinstance(msg, ResultMessage):
            sdk_session_id = msg.session_id
            if msg.result:
                print(msg.result)

    if not sdk_session_id:
        print("ERROR: no session_id captured")
        return

    # --- Turn 2: Pick a change and implement it ---
    print(f"\n[Turn 2] Implementing the top improvement (session={sdk_session_id[:8]}...)...")
    async for msg in query(
        prompt=(
            "From the issues you found, pick the single most impactful one "
            "and implement it. Write the improved file. Then run "
            "'uv run ruff check --fix src/agent_interception/display/terminal.py && "
            "uv run ruff format src/agent_interception/display/terminal.py' "
            "to verify it passes lint."
        ),
        options=dataclasses.replace(base_opts, resume=sdk_session_id),
    ):
        if isinstance(msg, ResultMessage) and msg.result:
            print(msg.result)

    # --- Turn 3: Verify and summarize ---
    print(f"\n[Turn 3] Verifying the change (session={sdk_session_id[:8]}...)...")
    async for msg in query(
        prompt=(
            "Now run the full test suite with 'uv run pytest tests/ -q' to "
            "make sure nothing broke. Then read the file one more time and "
            "give a brief before/after summary of what changed."
        ),
        options=dataclasses.replace(base_opts, resume=sdk_session_id),
    ):
        if isinstance(msg, ResultMessage) and msg.result:
            print(msg.result)


if __name__ == "__main__":
    asyncio.run(main())
