"""Ask an agent to do a deep code review of the project.

Exercises: heavy tool use (Read, Glob, Grep), large context, streaming.
Run with: uv run python scripts/code_review.py

Rate-limit handling:
  * Uses safe_query() which retries on rate_limit_event (60/120/180s backoff).
  * Optional INTERCEPTOR_PRERUN_SLEEP=N env var delays the first call by N
    seconds — handy when re-running immediately after a previous 429 and
    the per-minute bucket is still drained.
"""

from __future__ import annotations

import asyncio
import os

from _common import CLI_PATH, WORK_DIR, banner, safe_query, start_session
from claude_agent_sdk import ClaudeAgentOptions


async def main() -> None:
    session_id = start_session("code-review")
    banner("Code Review — full codebase read + analysis", session_id)

    prerun = float(os.environ.get("INTERCEPTOR_PRERUN_SLEEP", "0") or "0")
    if prerun > 0:
        print(f"[pre-run] sleeping {prerun:.0f}s to let the rate-limit bucket refill…")
        await asyncio.sleep(prerun)

    async for msg in safe_query(
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
            max_turns=15,
            cwd=WORK_DIR,
        ),
    ):
        if hasattr(msg, "result"):
            print(msg.result)  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
