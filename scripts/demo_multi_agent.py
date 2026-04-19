"""
Demo: Multi-Agent Interaction Capture — hub-and-spoke pattern
==============================================================
Simulates an orchestrator that dispatches TWO independent subagents and
collects their results, producing a hub-and-spoke graph:

    orchestrator ──► subagent-1  (solar research)
         ▲               │
         └───────────────┘   result returned
         │
         └──────────────► subagent-2  (wind research)
                ▲               │
                └───────────────┘   result returned

Each agent is a separate anthropic.Anthropic client with:
  - its own session prefix in base_url   → different session_id
  - X-Agent-Role header                  → role visible in the graph
  - X-Interceptor-Conversation-Id header → links all agents in one conversation

The interleaved call order is what produces the correct edges:
  1. orchestrator → (edge) → subagent-1
  2. subagent-1   → (edge) → orchestrator   (result return)
  3. orchestrator → (edge) → subagent-2
  4. subagent-2   → (edge) → orchestrator   (result return)

Prerequisites:
  - ANTHROPIC_API_KEY must be set (or placed in scripts/.env)
  - Proxy must be running:  uv run python -m agent_interception start

Run with: uv run python scripts/demo_multi_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import anthropic
import httpx
from _common import PROXY_URL, banner

# ─── helpers ──────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def make_client(
    session_label: str, role: str, conversation_id: str
) -> tuple[anthropic.Anthropic, str]:
    """Create an Anthropic client routed through the proxy with agent metadata."""
    session_id = f"{session_label}-{uuid.uuid4().hex[:6]}"
    client = anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        base_url=f"{PROXY_URL}/_session/{session_id}",
        default_headers={
            "X-Agent-Role": role,
            "X-Interceptor-Conversation-Id": conversation_id,
        },
    )
    return client, session_id


# Tool definitions available to subagents
SEARCH_TOOL: anthropic.types.ToolParam = {
    "name": "search_energy_database",
    "description": (
        "Search a renewable-energy statistics database for current figures. "
        "Returns structured data including efficiency rates, capacity factors, "
        "and cost trends."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, e.g. 'solar PV efficiency 2024'",
            },
            "category": {
                "type": "string",
                "enum": ["solar", "wind", "hydro", "geothermal", "general"],
                "description": "Energy category to narrow the search",
            },
        },
        "required": ["query", "category"],
    },
}

# Canned responses returned for tool calls (no real API needed)
_TOOL_RESPONSES: dict[str, str] = {
    "solar": (
        '{"source": "IEA 2024", "solar_pv_efficiency": "22-24% (commercial mono-PERC)", '
        '"record_lab_efficiency": "29.4% (perovskite-silicon tandem)", '
        '"cost_per_watt_usd": 0.28, "installed_capacity_gw": 1600}'
    ),
    "wind": (
        '{"source": "IRENA 2024", "offshore_capacity_factor": "40-60%", '
        '"avg_turbine_rating_mw": 8.5, "lcoe_usd_per_mwh": 80, '
        '"global_offshore_capacity_gw": 72}'
    ),
}


async def call_agent(
    client: anthropic.Anthropic,
    session_id: str,
    role: str,
    prompt: str,
    tools: list[anthropic.types.ToolParam] | None = None,
) -> str:
    """Make one LLM call (with optional tool use) and return the final text.

    If the model returns a tool_use block, a fake result is injected and a
    follow-up call is made so the proxy captures the full tool call/result turn.
    """
    print(f"  [{role:12s}] session={session_id}  →  {prompt[:60]}…")
    loop = asyncio.get_event_loop()

    def _sync_call() -> str:
        kwargs: dict = dict(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        if tools:
            kwargs["tools"] = tools

        msg = client.messages.create(**kwargs)

        # If the model chose to call a tool, send back a fake result
        if msg.stop_reason == "tool_use":
            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            tool_results = []
            for tu in tool_uses:
                category = (tu.input or {}).get("category", "general")  # type: ignore[union-attr]
                result_text = _TOOL_RESPONSES.get(category, '{"data": "no data found"}')
                print(f"  [{role:12s}]   tool_call: {tu.name}({tu.input})")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_text,
                })

            # Second call: model synthesizes the tool results into a text reply
            follow_up = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                tools=tools,  # type: ignore[arg-type]
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": msg.content},  # type: ignore[list-item]
                    {"role": "user", "content": tool_results},
                ],
            )
            for block in follow_up.content:
                if block.type == "text":
                    return block.text
            return ""

        for block in msg.content:
            if block.type == "text":
                return block.text
        return ""

    text = await loop.run_in_executor(None, _sync_call)
    print(f"  [{role:12s}] got reply: {text[:80].strip()}…")
    return text


async def check_proxy() -> bool:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{PROXY_URL}/_interceptor/health", timeout=3.0)
            return r.status_code == 200
        except httpx.TransportError:
            return False


# ─── main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    conv_id = f"demo-conv-{uuid.uuid4().hex[:8]}"

    banner("Multi-Agent Demo — orchestrator dispatches two subagents")
    print(f"  conversation_id = {conv_id}")
    print()
    print("  Expected graph:")
    print("    orchestrator ──► subagent-1 (solar efficiency)")
    print("         ▲               │")
    print("         └───────────────┘  (result returned)")
    print("         │")
    print("         └──────────────► subagent-2 (wind energy)")
    print()

    # ── pre-flight ────────────────────────────────────────────────────────────
    if not await check_proxy():
        print(f"\nERROR: proxy not reachable at {PROXY_URL}")
        print("  Start it with:  uv run python -m agent_interception start")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\nERROR: ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    # ── create one client per agent role ──────────────────────────────────────
    orch_client,  orch_session  = make_client("orchestrator", "orchestrator", conv_id)
    sub1_client,  sub1_session  = make_client("subagent-1",   "subagent",     conv_id)
    sub2_client,  sub2_session  = make_client("subagent-2",   "subagent",     conv_id)

    # ── step 1: orchestrator plans and dispatches subagent-1 ─────────────────
    section("Step 1 — Orchestrator dispatches subagent-1 (solar research)")
    task1 = "Research the current efficiency rates of solar photovoltaic panels."
    await call_agent(
        orch_client, orch_session, "orchestrator",
        f"You are an orchestrator managing two research subagents. "
        f"In one sentence, give subagent-1 this task: {task1}",
    )

    # ── step 2: subagent-1 executes solar task ────────────────────────────────
    # Edge created: orchestrator → subagent-1
    section("Step 2 — Subagent-1 executes solar research (uses search tool)")
    sub1_reply = await call_agent(
        sub1_client, sub1_session, "subagent-1",
        f"You are a research subagent with access to an energy database. "
        f"Task: {task1} "
        "Use the search_energy_database tool to look up current figures, "
        "then give a 2-sentence answer with one key fact.",
        tools=[SEARCH_TOOL],
    )

    # ── step 3: orchestrator collects sub1 result, dispatches subagent-2 ─────
    # Edge created: subagent-1 → orchestrator  (result return)
    section("Step 3 — Orchestrator reviews sub1 result, dispatches subagent-2 (wind research)")
    task2 = "Research the current capacity factor of offshore wind turbines."
    await call_agent(
        orch_client, orch_session, "orchestrator",
        f"Subagent-1 reported: '{sub1_reply[:150]}'. "
        f"Good. Now in one sentence, give subagent-2 this task: {task2}",
    )

    # ── step 4: subagent-2 executes wind task ────────────────────────────────
    # Edge created: orchestrator → subagent-2
    section("Step 4 — Subagent-2 executes wind research (uses search tool)")
    sub2_reply = await call_agent(
        sub2_client, sub2_session, "subagent-2",
        f"You are a research subagent with access to an energy database. "
        f"Task: {task2} "
        "Use the search_energy_database tool to look up current figures, "
        "then give a 2-sentence answer with one key fact.",
        tools=[SEARCH_TOOL],
    )

    # ── step 5: orchestrator synthesizes both results ────────────────────────
    # Edge created: subagent-2 → orchestrator  (result return)
    section("Step 5 — Orchestrator synthesizes both results")
    await call_agent(
        orch_client, orch_session, "orchestrator",
        f"Subagent-1 (solar): '{sub1_reply[:100]}'. "
        f"Subagent-2 (wind): '{sub2_reply[:100]}'. "
        "In two sentences, summarize the combined findings.",
    )

    # ── small pause for the proxy to flush writes ─────────────────────────────
    await asyncio.sleep(0.5)

    # ── query the agent-graph endpoint ───────────────────────────────────────
    section("Step 6 — Query agent-graph")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{PROXY_URL}/api/conversations/{conv_id}/agent-graph",
            timeout=5.0,
        )
        print(f"  HTTP {r.status_code}")

        if r.status_code == 200:
            graph = r.json()
            print(json.dumps(graph, indent=2))

            section("Summary")
            nodes = graph["nodes"]
            edges = graph["edges"]
            print(f"  Agents   : {len(nodes)}")
            for n in nodes:
                print(f"    • {n['session_id']:35s}  role={n['agent_role'] or '?':13s}  "
                      f"calls={n['interaction_count']}  "
                      f"tokens={n['total_tokens']}")
            print(f"\n  Handoffs : {len(edges)}")
            for e in edges:
                print(f"    • {e['from_session_id']} → {e['to_session_id']}"
                      f"  turn={e['turn_number']}")

            print()
            orch_outgoing = [e for e in edges if "orchestrator" in e["from_session_id"]]
            if len(nodes) == 3 and len(orch_outgoing) >= 2:
                print("  ✓ Orchestrator dispatches two subagents — graph is correct!")
                print("\n  Open http://localhost:8080/_ui/ → Multi-Agent tab")
                print(f"  Pick conversation:  {conv_id}")
            else:
                print(f"  NOTE: expected 3 nodes and ≥2 outgoing orchestrator edges, "
                      f"got {len(nodes)} nodes / {len(orch_outgoing)} orch→* edges")
        else:
            print(f"  Unexpected response: {r.text}")


if __name__ == "__main__":
    asyncio.run(main())
