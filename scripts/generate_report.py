"""Run tests, generate a file, then read it back — exercises Bash + Write.

Exercises: Bash (running pytest), Write (creating a report file),
Read (verifying the output), single conversation with many tool types.
Run with: uv run python scripts/generate_report.py
"""

from __future__ import annotations

import asyncio

from _common import CLI_PATH, WORK_DIR, banner, safe_query, start_session
from claude_agent_sdk import ClaudeAgentOptions


async def main() -> None:
    session_id = start_session("generate-report")
    banner("Generate Report — run tests + create summary file", session_id)

    options = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        allowed_tools=["Bash", "Read", "Write", "Glob"],
        permission_mode="bypassPermissions",
        max_turns=15,
        cwd=WORK_DIR,
    )
    async for msg in safe_query(
        prompt=(
            "Do the following steps:\n"
            "1. Run 'uv run pytest tests/ --co -q' to discover all test cases\n"
            "2. Run 'uv run pytest tests/ -v --tb=short' to run the full suite\n"
            "3. Use Glob to count the .py files in src/ and tests/\n"
            "4. Write a file called 'project_report.md' with:\n"
            "   - Total number of source files and test files\n"
            "   - Full list of test names, grouped by module\n"
            "   - Pass/fail status for each\n"
            "   - A one-paragraph project summary based on what you observed\n"
            "5. Read back the file you created and confirm it looks right"
        ),
        options=options,
    ):
        if hasattr(msg, "result"):
            print(msg.result)


if __name__ == "__main__":
    asyncio.run(main())
