# pyright: reportAttributeAccessIssue=false
"""Config ChemCrow-Py: Claude Haiku 4.5 + Python RDKit/PubChem tools.

Built on `claude-agent-sdk` (NOT the raw `anthropic` SDK) so this routes through
Claude Code Pro-plan tokens. Tools run in-process via an SDK MCP server.

Span hierarchy emitted per query:
    agent.query                      (root)
      ├── llm.generate (turn 0)      (one per LLM round-trip)
      ├── tool.lookup_molecule       (PubChem REST + on-disk cache)
      ├── llm.generate (turn 1)
      ├── tool.smiles_to_3d          (RDKit ETKDG embed + MMFF94 optimize)
      ├── llm.generate (turn 2)
      ├── tool.compute_descriptors   (RDKit Descriptors / Lipinski / Crippen)
      └── llm.generate (turn 3)      (final answer)

llm.generate timing: the SDK abstracts when API requests are sent, so we compute
each round's wall_time as (AssistantMessage arrival) - (last boundary), where
boundary is either the root start or the previous tool span's end. This is
attribution-honest: it includes any tool->API enqueue overhead in the LLM span,
which is the conservative direction (over-counts LLM, never tools).
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import time
from pathlib import Path
from typing import Any

from benchmark.obs import Observer
from benchmark.tools.chemcrow import (
    compute_descriptors,
    lookup_molecule,
    smiles_to_3d,
)

# Lazy SDK import: keeps this module importable on systems where claude-agent-sdk
# is missing (e.g. CI lint passes without the chemcrow extras installed).
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        UserMessage,
        create_sdk_mcp_server,
        query,
        tool,
    )

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on broken installs
    _SDK_AVAILABLE = False

LABEL = "ChemCrow Py"
DEFAULT_OUT_DIR = Path("benchmark/output/chemcrow")

_DEFAULT_MODEL = "claude-haiku-4-5"
_MAX_TURNS = 10

SYSTEM_PROMPT = (
    "You are a chemistry research assistant. To answer questions about molecules, "
    "use the tools provided to look up molecules by name, generate 3D structures "
    "from SMILES, and compute molecular descriptors. Use tools systematically: "
    "look up the molecule first, then generate the 3D structure, then compute "
    "descriptors. Report results clearly."
)

# Tool functions need the active Observer to emit spans. claude-agent-sdk @tool
# decorators take a single args dict, so we thread the Observer in via a
# contextvar set by run().
_CURRENT_OBS: contextvars.ContextVar[Observer | None] = contextvars.ContextVar(
    "chemcrow_obs", default=None
)


def _obs() -> Observer:
    obs = _CURRENT_OBS.get()
    if obs is None:
        raise RuntimeError("ChemCrow tool called outside of run() context")
    return obs


def _build_tools() -> list[Any]:
    """Build SDK tool wrappers around our instrumented Python tools.

    Each wrapper:
      - extracts kwargs from the SDK-supplied args dict
      - calls the underlying instrumented tool (which emits the obs span)
      - serializes the result as the JSON content block the SDK expects
    """
    if not _SDK_AVAILABLE:
        return []

    @tool(
        "lookup_molecule",
        "Look up a molecule by common name on PubChem and return its canonical "
        "SMILES and molecular weight. Use this first to get a SMILES string from "
        "a name like 'aspirin' or 'paclitaxel'.",
        {"name": str},
    )
    async def _lookup_molecule_tool(args):  # type: ignore[no-untyped-def]
        result = lookup_molecule(args["name"], _obs())
        return _as_text_content(result)

    @tool(
        "smiles_to_3d",
        "Generate a 3D conformer for a SMILES string using RDKit's ETKDG embed "
        "+ MMFF94 optimization. Returns atom count, heavy-atom count, and energy. "
        "Pass the SMILES returned by lookup_molecule.",
        {"smiles": str},
    )
    async def _smiles_to_3d_tool(args):  # type: ignore[no-untyped-def]
        result = smiles_to_3d(args["smiles"], _obs())
        # Strip coords from the LLM-visible payload (heavy, not useful in answer).
        slim = {k: v for k, v in result.items() if k != "coords"}
        return _as_text_content(slim)

    @tool(
        "compute_descriptors",
        "Compute molecular descriptors from a SMILES string: molecular weight, "
        "logP, TPSA, heavy-atom count, and number of rotatable bonds. Pure RDKit, "
        "no I/O. Pass the SMILES returned by lookup_molecule.",
        {"smiles": str},
    )
    async def _compute_descriptors_tool(args):  # type: ignore[no-untyped-def]
        result = compute_descriptors(args["smiles"], _obs())
        return _as_text_content(result)

    return [_lookup_molecule_tool, _smiles_to_3d_tool, _compute_descriptors_tool]


def _as_text_content(payload: Any) -> dict:
    import json as _json

    return {"content": [{"type": "text", "text": _json.dumps(payload, default=str)}]}


def run(query_rec: dict, obs: Observer) -> str:
    """Run one ChemCrow query end-to-end.

    Returns the final assistant text. Side effect: emits one trace JSON via
    `obs` covering agent.query / llm.generate / tool.* spans.
    """
    if not _SDK_AVAILABLE:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .[chemcrow]` "
            "or `uv pip install claude-agent-sdk` and retry."
        )

    query_text = query_rec.get("query_text") or query_rec.get("question") or ""
    return asyncio.run(_run_async(query_text, obs))


async def _run_async(query_text: str, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    tools = _build_tools()
    server = create_sdk_mcp_server(name="chemcrow_tools", tools=tools)

    debug_log: list[str] = []

    def _stderr_sink(line: str) -> None:
        debug_log.append(line)

    # Scrub ANTHROPIC_API_KEY before the SDK spawns the CLI. With it set,
    # the CLI prefers metered API auth; for Pro-plan billing the CLI must
    # see an unset key and fall back to its OAuth credentials. The metered
    # key in benchmark/.env is also unfunded in this environment, so leaving
    # it set produces "Credit balance is too low" errors instead of the
    # expected Pro-plan flow. ClaudeAgentOptions.env merges with os.environ
    # rather than replacing keys, so popping from os.environ is the way.
    os.environ.pop("ANTHROPIC_API_KEY", None)

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        max_turns=_MAX_TURNS,
        mcp_servers={"chemcrow": server},
        allowed_tools=[
            "mcp__chemcrow__lookup_molecule",
            "mcp__chemcrow__smiles_to_3d",
            "mcp__chemcrow__compute_descriptors",
        ],
        permission_mode="bypassPermissions",
        stderr=_stderr_sink,
    )

    final_text_chunks: list[str] = []
    turn_index = 0

    token = _CURRENT_OBS.set(obs)
    try:
        with obs.root(query_text=query_text) as root:
            # time.time_ns() not monotonic_ns(): OTel span timestamps are epoch
            # ns so the synthetic llm.generate spans must also be epoch ns to
            # sort correctly under the root in the flushed trace JSON.
            boundary_ns = time.time_ns()
            cpu_boundary_ns = time.process_time_ns()
            num_tool_calls = 0
            total_input_tokens = 0
            total_output_tokens = 0
            total_cost_usd = 0.0

            try:
                stream = query(prompt=query_text, options=options)
            except Exception as e:
                root.set("agent.error", f"query_init: {e!r}")
                raise

            # The CLI streams a single API response as multiple AssistantMessage
            # chunks (one per content block: thinking, text, tool_use). We
            # aggregate them into one llm.generate span per real API turn,
            # closed when the turn ends (next UserMessage tool result, or
            # ResultMessage at the very end).
            turn_active = False
            turn_last_ns = 0
            turn_last_cpu_ns = 0
            turn_model = model
            turn_has_tool_use = False

            def close_llm_turn() -> None:
                nonlocal turn_active, turn_has_tool_use, turn_index
                nonlocal boundary_ns, cpu_boundary_ns
                if not turn_active:
                    return
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=boundary_ns,
                    end_ns=turn_last_ns,
                    cpu_start_ns=cpu_boundary_ns,
                    cpu_end_ns=turn_last_cpu_ns,
                    **{
                        "llm.model": turn_model,
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": turn_index,
                        "llm.has_tool_use": turn_has_tool_use,
                        "llm.stop_reason": "tool_use" if turn_has_tool_use else "end_turn",
                    },
                )
                turn_index += 1
                turn_active = False
                turn_has_tool_use = False
                boundary_ns = turn_last_ns
                cpu_boundary_ns = turn_last_cpu_ns

            async for msg in stream:
                if isinstance(msg, AssistantMessage):
                    now = time.time_ns()
                    cpu_now = time.process_time_ns()
                    turn_active = True
                    turn_last_ns = now
                    turn_last_cpu_ns = cpu_now
                    turn_model = msg.model or model
                    if any(isinstance(b, ToolUseBlock) for b in msg.content):
                        turn_has_tool_use = True
                    text_chunks = [b.text for b in msg.content if isinstance(b, TextBlock)]
                    if text_chunks:
                        final_text_chunks.extend(text_chunks)
                elif isinstance(msg, UserMessage) and msg.tool_use_result is not None:
                    # Tool just finished. Close the LLM round it preceded, then
                    # reset the boundary so the next round's wall time excludes
                    # the tool's runtime (the tool span is the source of truth).
                    close_llm_turn()
                    num_tool_calls += 1
                    boundary_ns = time.time_ns()
                    cpu_boundary_ns = time.process_time_ns()
                elif isinstance(msg, ResultMessage):
                    # Final turn ends here. Close any in-flight LLM round, then
                    # aggregate usage onto the root for analysis.
                    close_llm_turn()
                    usage = getattr(msg, "usage", None) or {}
                    total_input_tokens = int(usage.get("input_tokens") or 0)
                    total_output_tokens = int(usage.get("output_tokens") or 0)
                    total_cost_usd = float(getattr(msg, "total_cost_usd", 0.0) or 0.0)
                    root.set("agent.num_turns", int(getattr(msg, "num_turns", 0) or 0))
                    root.set("agent.total_input_tokens", total_input_tokens)
                    root.set("agent.total_output_tokens", total_output_tokens)
                    root.set("agent.total_cost_usd", total_cost_usd)
                    root.set("agent.duration_ms", float(getattr(msg, "duration_ms", 0.0) or 0.0))
                    root.set("agent.duration_api_ms", float(getattr(msg, "duration_api_ms", 0.0) or 0.0))
                    is_err = bool(getattr(msg, "is_error", False))
                    root.set("agent.is_error", is_err)
                    root.set("agent.truncated", turn_index >= _MAX_TURNS and is_err)

            # Belt-and-suspenders: if the stream ended without ResultMessage,
            # still emit the trailing llm.generate span.
            close_llm_turn()
            root.set("agent.num_tool_calls", num_tool_calls)
            if debug_log:
                # Trim aggressively; the CLI is chatty about session bootstrap.
                tail = "\n".join(debug_log[-40:])
                root.set("agent.cli_stderr_tail", tail[-4000:])
    finally:
        _CURRENT_OBS.reset(token)

    return "\n".join(final_text_chunks).strip()


# ----------------------------------------------------------------------
# CLI shim used by the acceptance checklist:
#   python -m benchmark.configs.config_chemcrow_py --query-id 0
# Also accepts --query-id qNNN form.
# ----------------------------------------------------------------------
def _cli() -> int:
    import argparse
    import json
    import sys

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--query-id", default="0", help="Query index (0..N) or query_id (e.g. q011)")
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("benchmark/queries/chemcrow_20.json"),
    )
    # --output is the canonical name (matches Phase-2 sweep harness invocation);
    # --out is kept as an alias for back-compat with Phase-1 docs.
    parser.add_argument("--out", "--output", dest="out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--config", default="chemcrow_py",
                        help="config name written into the trace JSON")
    parser.add_argument("--forward-to", default=os.environ.get("OBS_FORWARD_TO", "") or None)
    args = parser.parse_args()

    load_dotenv(Path("benchmark/.env"))

    blob = json.loads(args.queries.read_text())
    queries = blob["queries"]
    qid = args.query_id
    if qid.isdigit():
        idx = int(qid)
        if idx < 0 or idx >= len(queries):
            print(f"--query-id {idx} out of range (have {len(queries)} queries)", file=sys.stderr)
            return 2
        q = queries[idx]
    else:
        candidates = [c for c in queries if c["query_id"] == qid]
        if not candidates:
            print(f"--query-id {qid!r} not found", file=sys.stderr)
            return 2
        q = candidates[0]

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    obs = Observer(
        config=args.config,
        query_id=q["query_id"],
        out_dir=out_dir,
        forward_to=args.forward_to or None,
        label=q.get("label"),
    )
    answer = run(q, obs)
    print(f"=== {q['query_id']} ({q.get('label','')}, {q.get('molecule_name','')}) ===")
    print((answer or "").strip()[:600])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
