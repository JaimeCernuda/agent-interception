# pyright: reportAttributeAccessIssue=false
"""Config SWE-Agent-Py: Claude Haiku 4.5 + Python bash/file tools.

Built on `claude-agent-sdk` (NOT raw `anthropic`) so this routes through
Claude Code Pro-plan tokens — same transport as ChemCrow-Py. Three tools:

  - bash_run  (with bash_spawn / bash_work child spans)
  - read_file
  - write_file

Span hierarchy emitted per query:

    agent.query                 (root, attr agent.cpu_time_ms)
      ├── llm.generate (turn 0)
      ├── tool.bash_run
      │     ├── tool.bash_spawn
      │     └── tool.bash_work
      ├── llm.generate (turn 1)
      ├── tool.read_file
      ├── llm.generate (turn 2)
      ├── tool.write_file
      └── llm.generate (turn 3)  (final answer)

The agent.cpu_time_ms attribute on the root span is computed via
resource.getrusage(RUSAGE_SELF) deltas around the entire query. This is the
diagnostic that tells us whether the workload exercises real CPU work in the
agent process (high CPU/wall ratio) or is I/O-bound (low ratio).
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
from benchmark.tools.sweagent import bash_run, read_file, write_file

# Lazy SDK import: keeps this module importable on systems where claude-agent-sdk
# is missing.
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

LABEL = "SWE-Agent Py"
DEFAULT_OUT_DIR = Path("benchmark/output/sweagent")

_DEFAULT_MODEL = "claude-haiku-4-5"
_MAX_TURNS = 30  # SWE-Agent queries take more turns than ChemCrow.

SYSTEM_PROMPT = (
    "You are a software-engineering assistant working in a workspace directory. "
    "You have three tools:\n"
    "  - bash_run: run a shell command. Use this for grep, awk, ls, head, find, "
    "and any custom test runners (e.g. `bash runtests.sh`).\n"
    "  - read_file: read a file's contents.\n"
    "  - write_file: write a file's contents (overwrites).\n"
    "Always use these tools — never claim to have run a command without calling "
    "bash_run. After running tools, REASON over their output: parse, aggregate, "
    "filter, then answer. Be concise in your final answer."
)

# Tool functions get the active Observer + workspace via contextvars.
_CURRENT_OBS: contextvars.ContextVar[Observer | None] = contextvars.ContextVar(
    "sweagent_obs", default=None
)
_CURRENT_WS: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "sweagent_ws", default=None
)


def _obs() -> Observer:
    o = _CURRENT_OBS.get()
    if o is None:
        raise RuntimeError("SWE-Agent tool called outside of run() context")
    return o


def _ws() -> Path:
    w = _CURRENT_WS.get()
    if w is None:
        raise RuntimeError("SWE-Agent tool called outside of run() context (no workspace)")
    return w


def _measure_self_cpu_ms() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return (r.ru_utime + r.ru_stime) * 1000.0


def _build_tools() -> list[Any]:
    if not _SDK_AVAILABLE:
        return []

    @tool(
        "bash_run",
        "Run a bash command inside the workspace directory. Returns "
        "{stdout, stderr, exit_code, timed_out}. Default timeout is 30s. "
        "Use shell metacharacters freely (|, >, &&) — they are wrapped with "
        "bash -c automatically.",
        {"command": str},
    )
    async def _bash_run_tool(args):  # type: ignore[no-untyped-def]
        result = bash_run(args["command"], _ws(), _obs())
        return _as_text_content(result)

    @tool(
        "read_file",
        "Read up to 50 KB from a file inside the workspace. Returns "
        "{content, truncated, size_bytes}. Path is relative to the workspace.",
        {"path": str},
    )
    async def _read_file_tool(args):  # type: ignore[no-untyped-def]
        result = read_file(args["path"], _ws(), _obs())
        return _as_text_content(result)

    @tool(
        "write_file",
        "Write content to a file inside the workspace (overwrites). Creates "
        "parent directories. Path is relative to the workspace.",
        {"path": str, "content": str},
    )
    async def _write_file_tool(args):  # type: ignore[no-untyped-def]
        result = write_file(args["path"], args["content"], _ws(), _obs())
        return _as_text_content(result)

    return [_bash_run_tool, _read_file_tool, _write_file_tool]


def _as_text_content(payload: Any) -> dict:
    import json as _json

    return {"content": [{"type": "text", "text": _json.dumps(payload, default=str)}]}


def run(query_rec: dict, obs: Observer, workspace_dir: Path) -> str:
    """Run one SWE-Agent query. Returns the final assistant text.

    workspace_dir must already exist and contain the per-query fixtures.
    """
    if not _SDK_AVAILABLE:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .[sweagent]` "
            "or `uv pip install claude-agent-sdk` and retry."
        )

    query_text = query_rec.get("query_text") or query_rec.get("question") or ""
    return asyncio.run(_run_async(query_text, obs, workspace_dir))


async def _run_async(query_text: str, obs: Observer, workspace_dir: Path) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    tools = _build_tools()
    server = create_sdk_mcp_server(name="sweagent_tools", tools=tools)

    debug_log: list[str] = []

    def _stderr_sink(line: str) -> None:
        debug_log.append(line)

    # Same Pro-plan auth pattern as ChemCrow: scrub ANTHROPIC_API_KEY so the
    # CLI falls back to its OAuth credentials.
    os.environ.pop("ANTHROPIC_API_KEY", None)

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        max_turns=_MAX_TURNS,
        mcp_servers={"sweagent": server},
        allowed_tools=[
            "mcp__sweagent__bash_run",
            "mcp__sweagent__read_file",
            "mcp__sweagent__write_file",
        ],
        permission_mode="bypassPermissions",
        # cwd nudges the CLI process; tools internally enforce the workspace
        # again via _safe_workspace_path so symlinks/.. in arguments cannot escape.
        cwd=str(workspace_dir),
        stderr=_stderr_sink,
    )

    final_text_chunks: list[str] = []
    turn_index = 0

    obs_token = _CURRENT_OBS.set(obs)
    ws_token = _CURRENT_WS.set(workspace_dir)
    try:
        agent_cpu_start = _measure_self_cpu_ms()
        with obs.root(query_text=query_text) as root:
            root.set("agent.workspace_dir", str(workspace_dir))
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
            # Critical metric: how much CPU did the agent process actually burn?
            root.set("agent.cpu_time_ms", _measure_self_cpu_ms() - agent_cpu_start)
            if debug_log:
                tail = "\n".join(debug_log[-40:])
                root.set("agent.cli_stderr_tail", tail[-4000:])
    finally:
        _CURRENT_OBS.reset(obs_token)
        _CURRENT_WS.reset(ws_token)

    return "\n".join(final_text_chunks).strip()


# ---------------------------------------------------------------------------
# CLI shim:
#   python -m benchmark.configs.config_sweagent_py --query-id q01
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
        default=Path("benchmark/queries/sweagent_20.json"),
    )
    parser.add_argument("--out", "--output", dest="out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--config", default="sweagent_py",
                        help="config name written into the trace JSON")
    parser.add_argument("--forward-to", default=os.environ.get("OBS_FORWARD_TO", "") or None)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("benchmark/queries"),
        help="root where workspace_dir paths from the queries file are resolved",
    )
    parser.add_argument(
        "--workspace-copy-root",
        type=Path,
        default=None,
        help="if set, copy the per-query workspace into this directory before "
             "running so the agent can mutate files without dirtying the fixture. "
             "Defaults to a sibling of --out.",
    )
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

    src_ws = (args.workspace_root / q["workspace_dir"]).resolve()
    if not src_ws.exists():
        print(f"workspace not found: {src_ws} — run generate_sweagent_workspaces.py first",
              file=sys.stderr)
        return 2

    copy_root = args.workspace_copy_root or (args.out / "workspaces")
    copy_root.mkdir(parents=True, exist_ok=True)
    run_ws = copy_root / q["query_id"]
    if run_ws.exists():
        import shutil
        shutil.rmtree(run_ws)
    import shutil
    shutil.copytree(src_ws, run_ws)

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    obs = Observer(
        config=args.config,
        query_id=q["query_id"],
        out_dir=out_dir,
        forward_to=args.forward_to or None,
        label=q.get("label"),
    )
    answer = run(q, obs, run_ws)
    print(f"=== {q['query_id']} ({q.get('category','?')} / {q.get('label','')}) ===")
    print((answer or "").strip()[:600])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
