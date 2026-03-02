"""Multi-turn design discussion about extending the proxy.

Exercises: extended thinking, multi-turn session, heavy reasoning,
large context from reading multiple files, no writes (read-only).
Run with: uv run python scripts/design_discussion.py
"""

from __future__ import annotations

import asyncio
import dataclasses

from _common import CLI_PATH, WORK_DIR, banner, start_session
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage


async def main() -> None:
    session_id = start_session("design-discussion")
    banner("Design Discussion — multi-turn architecture brainstorm", session_id)

    sdk_session_id: str | None = None
    opts = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        max_turns=15,
        cwd=WORK_DIR,
    )

    # --- Turn 1: Understand the streaming approach ---
    print("[Turn 1] Understanding the current streaming design...")
    async for msg in query(
        prompt=(
            "Read proxy/streaming.py and proxy/handler.py. Explain in detail "
            "how the SSE stream interception works — how raw bytes flow from "
            "upstream through the interceptor to the client, and where the "
            "buffering + parsing happens. Be precise about the async flow."
        ),
        options=opts,
    ):
        if isinstance(msg, ResultMessage):
            sdk_session_id = msg.session_id
            if msg.result:
                print(msg.result)

    if not sdk_session_id:
        print("ERROR: no session_id captured")
        return

    # --- Turn 2: Explore a hard design problem ---
    print(f"\n[Turn 2] Exploring WebSocket support design (session={sdk_session_id[:8]}...)...")
    async for msg in query(
        prompt=(
            "Given what you now know about the architecture, think deeply: "
            "if we wanted to intercept WebSocket connections (for MCP server "
            "traffic), what would the design look like? Consider:\n"
            "- Starlette's WebSocket support vs the current HTTP handler\n"
            "- Bidirectional message logging (client->server and server->client)\n"
            "- How StreamInterceptor would need to change\n"
            "- Storage schema implications\n"
            "Propose a concrete plan with file-level changes."
        ),
        options=dataclasses.replace(opts, resume=sdk_session_id),
    ):
        if isinstance(msg, ResultMessage) and msg.result:
            print(msg.result)

    # --- Turn 3: Poke holes in the design ---
    print(f"\n[Turn 3] Critiquing the proposal (session={sdk_session_id[:8]}...)...")
    async for msg in query(
        prompt=(
            "Now be your own devil's advocate. What are the three biggest "
            "risks or failure modes in the WebSocket plan you just proposed? "
            "For each, suggest a mitigation. Also read providers/registry.py "
            "and explain how the detection logic would need to change to "
            "distinguish WebSocket upgrade requests from normal HTTP."
        ),
        options=dataclasses.replace(opts, resume=sdk_session_id),
    ):
        if isinstance(msg, ResultMessage) and msg.result:
            print(msg.result)


if __name__ == "__main__":
    asyncio.run(main())
