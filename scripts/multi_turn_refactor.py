"""Multi-turn conversation: iteratively refactor a module.

Exercises: multi-turn with session resume, Read + Write + Bash,
extended thinking on design decisions, context accumulation.
Run with: uv run python scripts/multi_turn_refactor.py

Rate-limit handling:
  * Uses safe_query() — retries on rate_limit_event (60/120/180s backoff).
  * Sleeps INTERCEPTOR_INTER_TURN_SLEEP seconds between turns (default 45s)
    so the per-minute input-token bucket refills between API sessions.
  * Optional INTERCEPTOR_PRERUN_SLEEP=N delays the first call by N seconds.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os

from _common import CLI_PATH, WORK_DIR, banner, safe_query, start_session
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import ResultMessage

INTER_TURN_SLEEP_S = float(os.environ.get("INTERCEPTOR_INTER_TURN_SLEEP", "45") or "0")
PRERUN_SLEEP_S = float(os.environ.get("INTERCEPTOR_PRERUN_SLEEP", "0") or "0")


async def _cooldown(seconds: float, label: str) -> None:
    if seconds <= 0:
        return
    print(f"[{label}] sleeping {seconds:.0f}s to let the rate-limit bucket refill…")
    await asyncio.sleep(seconds)


async def main() -> None:
    session_id = start_session("multi-turn-refactor")
    banner("Multi-Turn Refactor — 3 rounds with session resume", session_id)

    await _cooldown(PRERUN_SLEEP_S, "pre-run")

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
    async for msg in safe_query(
        prompt=(
            f"Read {WORK_DIR}/src/agent_interception/display/terminal.py carefully. "
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

    await _cooldown(INTER_TURN_SLEEP_S, "turn 1 → 2")

    # --- Turn 2: Pick a change and implement it ---
    print(f"\n[Turn 2] Implementing the top improvement (session={sdk_session_id[:8]}...)...")
    async for msg in safe_query(
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

    await _cooldown(INTER_TURN_SLEEP_S, "turn 2 → 3")

    # --- Turn 3: Verify and summarize ---
    print(f"\n[Turn 3] Verifying the change (session={sdk_session_id[:8]}...)...")
    async for msg in safe_query(
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
