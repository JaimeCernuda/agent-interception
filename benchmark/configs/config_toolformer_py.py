# pyright: reportAttributeAccessIssue=false
"""Config Toolformer-Py: Claude Haiku 4.5 + a single calculator tool.

Built on `claude-agent-sdk` (NOT raw `anthropic`) so this routes through
Claude Code Pro-plan tokens — same transport as ChemCrow-Py and SWE-Agent-Py.
One tool: `calculator` (safe arithmetic via simpleeval, microseconds per call).

Phase-1 role in the thesis taxonomy:
  - LLM-orchestrated, dynamic-path, single-step workload (Raj et al. 2025).
  - The "tool" is microseconds; the LLM dominates wall time. This is the
    LLM-bottlenecked counterpart to ChemCrow's tool-bottlenecked case.

Span hierarchy emitted per query:

    agent.query                 (root, attr agent.cpu_time_ms)
      ├── llm.generate (turn 0)
      ├── tool.calculator       (one or more)
      ├── llm.generate (turn 1)
      ├── tool.calculator
      └── llm.generate (turn 2)  (final answer)

The agent.cpu_time_ms attribute on the root span is computed via
resource.getrusage(RUSAGE_SELF) deltas around the entire query — same
diagnostic as SWE-Agent-Py, kept consistent for cross-workload comparison.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import resource
import time
from pathlib import Path
from typing import Any

from benchmark.obs import Observer
from benchmark.tools.toolformer import calculator

# Lazy SDK import: keeps this module importable on systems where
# claude-agent-sdk is missing.
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
except ImportError:  # pragma: no cover
    _SDK_AVAILABLE = False

LABEL = "Toolformer Py"
DEFAULT_OUT_DIR = Path("benchmark/output/toolformer")

_DEFAULT_MODEL = "claude-haiku-4-5"
# These queries are simple word-problems. Eight tool-loop iterations is plenty
# — if the agent isn't done in 8, something is wrong.
_MAX_TURNS = 8

SYSTEM_PROMPT = (
    "You are a math problem solver. You have access to a calculator tool that "
    "evaluates arithmetic expressions. For each problem:\n"
    "1. Read the problem carefully and identify the arithmetic needed.\n"
    "2. Use the calculator tool to compute intermediate and final values.\n"
    "3. Respond with the final numeric answer.\n\n"
    "Use the calculator for ANY arithmetic — even simple operations. Do not "
    "do math in your head."
)

# Tool functions get the active Observer via contextvar set in run().
_CURRENT_OBS: contextvars.ContextVar[Observer | None] = contextvars.ContextVar(
    "toolformer_obs", default=None
)


def _obs() -> Observer:
    o = _CURRENT_OBS.get()
    if o is None:
        raise RuntimeError("Toolformer tool called outside of run() context")
    return o


def _measure_self_cpu_ms() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return (r.ru_utime + r.ru_stime) * 1000.0


def _build_tools() -> list[Any]:
    if not _SDK_AVAILABLE:
        return []

    @tool(
        "calculator",
        "Evaluate a numeric arithmetic expression and return the result. "
        "Supports +, -, *, /, **, parentheses, and the math functions sqrt, "
        "log, exp, sin, cos, tan, abs, min, max, pow. Constants pi and e are "
        "available. Returns {result: float, error: string|null}. On error "
        "(invalid expression, division by zero, etc.) result is null and "
        "error describes the problem; the caller can retry with a corrected "
        "expression.",
        {"expression": str},
    )
    async def _calculator_tool(args):  # type: ignore[no-untyped-def]
        result = calculator(args["expression"], _obs())
        return _as_text_content(result)

    return [_calculator_tool]


def _as_text_content(payload: Any) -> dict:
    import json as _json

    return {"content": [{"type": "text", "text": _json.dumps(payload, default=str)}]}


def run(query_rec: dict, obs: Observer) -> str:
    """Run one Toolformer query end-to-end. Returns the final assistant text."""
    if not _SDK_AVAILABLE:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .[toolformer]` "
            "or `uv pip install claude-agent-sdk` and retry."
        )

    query_text = query_rec.get("question") or query_rec.get("query_text") or ""
    return asyncio.run(_run_async(query_text, obs))


async def _run_async(query_text: str, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    tools = _build_tools()
    server = create_sdk_mcp_server(name="toolformer_tools", tools=tools)

    debug_log: list[str] = []

    def _stderr_sink(line: str) -> None:
        debug_log.append(line)

    # Pro-plan auth pattern (same as ChemCrow / SWE-Agent): scrub
    # ANTHROPIC_API_KEY so the CLI falls back to its OAuth credentials.
    os.environ.pop("ANTHROPIC_API_KEY", None)

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        max_turns=_MAX_TURNS,
        mcp_servers={"toolformer": server},
        allowed_tools=["mcp__toolformer__calculator"],
        permission_mode="bypassPermissions",
        stderr=_stderr_sink,
    )

    final_text_chunks: list[str] = []
    turn_index = 0

    obs_token = _CURRENT_OBS.set(obs)
    try:
        agent_cpu_start = _measure_self_cpu_ms()
        with obs.root(query_text=query_text) as root:
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
                root.set("agent.cpu_time_ms", _measure_self_cpu_ms() - agent_cpu_start)
                raise

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
                    close_llm_turn()
                    num_tool_calls += 1
                    boundary_ns = time.time_ns()
                    cpu_boundary_ns = time.process_time_ns()
                elif isinstance(msg, ResultMessage):
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

            close_llm_turn()
            root.set("agent.num_tool_calls", num_tool_calls)
            root.set("agent.cpu_time_ms", _measure_self_cpu_ms() - agent_cpu_start)
            if debug_log:
                tail = "\n".join(debug_log[-40:])
                root.set("agent.cli_stderr_tail", tail[-4000:])
    finally:
        _CURRENT_OBS.reset(obs_token)

    return "\n".join(final_text_chunks).strip()


# ---------------------------------------------------------------------------
# CLI shim:
#   python -m benchmark.configs.config_toolformer_py --query-id q01
# ---------------------------------------------------------------------------
def _cli() -> int:
    import argparse
    import json
    import sys

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--query-id", default="0",
                        help="Query index (0..N) or query_id (e.g. q01)")
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("benchmark/queries/toolformer_20.json"),
    )
    parser.add_argument("--out", "--output", dest="out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--config", default="toolformer_py",
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
        label=q.get("category"),
    )
    answer = run(q, obs)
    print(f"=== {q['query_id']} ({q.get('category','?')} / "
          f"expected={q.get('expected_answer','?')}) ===")
    print((answer or "").strip()[:600])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
