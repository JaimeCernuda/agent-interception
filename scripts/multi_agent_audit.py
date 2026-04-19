"""Multi-agent codebase audit — anthropic SDK, real tools, proper graph visualization.

Five agents share one X-Interceptor-Conversation-Id so the Multi-Agent tab shows
a connected hub-and-spoke graph.  The API key is read from ANTHROPIC_API_KEY
(set in scripts/.env or the environment — never hardcoded here).

Tools are REAL: files are actually read, grep/glob runs in-process, pytest
actually executes, and the final report is written to CODEBASE_AUDIT.md.

Hub-and-spoke graph:
    orchestrator ──► scanner      (Glob + Grep)
         ▲               │
         ◄───────────────┘
         ├──────────────► api_auditor  (Read + Grep)
         ◄───────────────┘
         ├──────────────► test_runner  (Bash + Read)
         ◄───────────────┘
         └──────────────► reporter     (Read + Write)

Prerequisites:
  - ANTHROPIC_API_KEY set (or in scripts/.env)
  - Proxy running:  uv run python -m agent_interception start

Run with: uv run python scripts/multi_agent_audit.py
"""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import subprocess
import sys
import uuid
from pathlib import Path

import anthropic
import httpx

from _common import PROXY_URL, WORK_DIR, banner

MODEL = "claude-haiku-4-5-20251001"


# ── Tool schemas ───────────────────────────────────────────────────────────────

READ_FILE: anthropic.types.ToolParam = {
    "name": "read_file",
    "description": "Read a file and return its contents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path":      {"type": "string", "description": "Absolute file path"},
            "max_lines": {"type": "integer", "description": "Max lines to return (default 120)"},
        },
        "required": ["path"],
    },
}

GLOB_FILES: anthropic.types.ToolParam = {
    "name": "glob_files",
    "description": "Find files matching a glob pattern. Returns matching paths.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern":  {"type": "string", "description": "Glob pattern, e.g. 'src/**/*.py'"},
            "base_dir": {"type": "string", "description": "Base directory (default: project root)"},
        },
        "required": ["pattern"],
    },
}

SEARCH_CODE: anthropic.types.ToolParam = {
    "name": "search_code",
    "description": "Search for a regex pattern across source files. Returns matching lines.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern":   {"type": "string", "description": "Regex or literal text"},
            "path":      {"type": "string", "description": "Directory or file to search (default: project root)"},
            "file_glob": {"type": "string", "description": "Restrict to files matching this glob, e.g. '*.py'"},
        },
        "required": ["pattern"],
    },
}

RUN_COMMAND: anthropic.types.ToolParam = {
    "name": "run_command",
    "description": "Run a shell command in the project root and return stdout + stderr.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
        },
        "required": ["command"],
    },
}

WRITE_FILE: anthropic.types.ToolParam = {
    "name": "write_file",
    "description": "Write content to a file, overwriting if it exists.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path":    {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Full content to write"},
        },
        "required": ["path", "content"],
    },
}


# ── Tool execution ─────────────────────────────────────────────────────────────

def _run_tool(name: str, args: dict) -> str:  # type: ignore[type-arg]
    try:
        if name == "read_file":
            p = Path(args["path"])
            max_lines = int(args.get("max_lines", 120))
            lines = p.read_text(errors="replace").splitlines(keepends=True)
            return "".join(lines[:max_lines])

        if name == "glob_files":
            base = args.get("base_dir", WORK_DIR)
            pattern = args["pattern"]
            full = pattern if pattern.startswith("/") else str(Path(base) / pattern)
            matches = sorted(_glob.glob(full, recursive=True))[:60]
            return "\n".join(matches) or "(no matches)"

        if name == "search_code":
            cmd = [
                "grep", "-rn",
                "--include", args.get("file_glob", "*"),
                args["pattern"],
                args.get("path", WORK_DIR),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return (r.stdout + r.stderr).strip()[:3000] or "(no matches)"

        if name == "run_command":
            r = subprocess.run(
                args["command"], shell=True, capture_output=True,
                text=True, timeout=120, cwd=WORK_DIR,
            )
            return (r.stdout + r.stderr).strip()[:4000] or "(no output)"

        if name == "write_file":
            p = Path(args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"Written {len(args['content'])} chars to {p}"

        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Tool error: {exc}"


# ── Agent factory & runner ─────────────────────────────────────────────────────

def _make_client(label: str, role: str, conv_id: str) -> tuple[anthropic.Anthropic, str]:
    """Create an Anthropic client routed through the proxy with graph-linking headers."""
    session_id = f"{label}-{uuid.uuid4().hex[:6]}"
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=f"{PROXY_URL}/_session/{session_id}",
        default_headers={
            "X-Agent-Role":                  role,
            "X-Interceptor-Conversation-Id": conv_id,
        },
    )
    return client, session_id


async def call_agent(
    client: anthropic.Anthropic,
    session_id: str,
    role: str,
    prompt: str,
    tools: list[anthropic.types.ToolParam],
    context: str = "",
) -> str:
    """Run one agent to completion, executing real tools on every tool_use stop."""
    full_prompt = f"Context from previous agents:\n{context}\n\n{prompt}" if context else prompt
    print(f"\n  [{role:13s}] session={session_id}  starting…")

    def _sync() -> str:
        import time
        messages: list[dict] = [{"role": "user", "content": full_prompt}]  # type: ignore[type-arg]
        for _ in range(12):
            # Retry on rate limit with exponential backoff
            for attempt in range(5):
                try:
                    resp = client.messages.create(
                        model=MODEL, max_tokens=2048, tools=tools, messages=messages,  # type: ignore[arg-type]
                    )
                    break
                except anthropic.RateLimitError:
                    wait = 60 * (attempt + 1)
                    print(f"  [{role:13s}]   rate limited — waiting {wait}s (attempt {attempt + 1}/5)…")
                    time.sleep(wait)
            else:
                raise RuntimeError(f"Rate limit persisted after 5 retries for agent '{role}'")
            if resp.stop_reason == "end_turn":
                return next((b.text for b in resp.content if b.type == "text"), "")

            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": list(resp.content)})  # type: ignore[list-item]
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        out = _run_tool(b.name, b.input)  # type: ignore[arg-type]
                        print(f"  [{role:13s}]   {b.name}() → {len(out)} chars")
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                messages.append({"role": "user", "content": results})
        return ""

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _sync)
    print(f"  [{role:13s}] done — {text[:160].replace(chr(10), ' ')}…")
    return text


# ── Proxy health check ─────────────────────────────────────────────────────────

async def check_proxy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{PROXY_URL}/_interceptor/health")
            return r.status_code == 200
    except httpx.TransportError:
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    conv_id = f"audit-{uuid.uuid4().hex[:8]}"

    banner("Multi-Agent Codebase Audit — 5 agents, real tools")
    print(f"  conversation_id : {conv_id}")
    print(f"  model           : {MODEL}")
    print(f"  work dir        : {WORK_DIR}\n")

    if not await check_proxy():
        print(f"ERROR: proxy not reachable at {PROXY_URL}")
        print("  Start it with:  uv run python -m agent_interception start")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (add it to scripts/.env)")
        sys.exit(1)

    # One client per agent — same conv_id, different session + role
    orch_client, orch_sid  = _make_client("orchestrator", "orchestrator", conv_id)
    scan_client, scan_sid  = _make_client("scanner",      "subagent",     conv_id)
    api_client,  api_sid   = _make_client("api-auditor",  "subagent",     conv_id)
    test_client, test_sid  = _make_client("test-runner",  "subagent",     conv_id)
    rep_client,  rep_sid   = _make_client("reporter",     "subagent",     conv_id)

    # ── 1. Orchestrator plans ─────────────────────────────────────────────────
    print("─" * 60)
    print("  1/5  Orchestrator  [read_file, glob_files]")
    print("─" * 60)
    orch_out = await call_agent(
        orch_client, orch_sid, "orchestrator",
        tools=[READ_FILE, GLOB_FILES],
        prompt=(
            f"You are the audit orchestrator for the project at {WORK_DIR}.\n"
            f"Use read_file to read {WORK_DIR}/pyproject.toml (max_lines=50).\n"
            "Use glob_files with pattern 'src/**/*.py' to count Python source files.\n"
            "Return a short project overview and a one-line task for each of the four "
            "subagents you will dispatch: scanner, api_auditor, test_runner, reporter."
        ),
    )

    # ── 2. Scanner maps structure ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  2/5  Scanner  [glob_files, search_code]")
    print("─" * 60)
    scan_out = await call_agent(
        scan_client, scan_sid, "subagent",
        tools=[GLOB_FILES, SEARCH_CODE],
        prompt=(
            f"You are the Scanner subagent. Project root: {WORK_DIR}\n\n"
            "glob_files: count Python files → pattern 'src/**/*.py'\n"
            "glob_files: count test files   → pattern 'tests/**/*.py'\n"
            "glob_files: count TS/TSX files → pattern 'frontend/src/**/*.ts'\n"
            "search_code: count class defs  → pattern '^class ', path=src/, file_glob='*.py'\n"
            "search_code: find TODO/FIXME   → pattern 'TODO|FIXME'\n\n"
            "Return file counts, class count, TODO count, and top-level package list."
        ),
    )

    # ── 3. Orchestrator reviews, dispatches api_auditor ──────────────────────
    print("\n" + "─" * 60)
    print("  3/5  Orchestrator reviews scan → dispatches api_auditor  [read_file]")
    print("─" * 60)
    await call_agent(
        orch_client, orch_sid, "orchestrator",
        tools=[READ_FILE],
        context=f"Scanner findings: {scan_out[:400]}",
        prompt=(
            "Briefly acknowledge the scanner results. "
            "Then state the task for the api_auditor subagent in one sentence."
        ),
    )

    # ── 4. API Auditor documents HTTP surface ─────────────────────────────────
    print("\n" + "─" * 60)
    print("  4/5  API Auditor  [read_file, search_code]")
    print("─" * 60)
    api_out = await call_agent(
        api_client, api_sid, "subagent",
        tools=[READ_FILE, SEARCH_CODE],
        prompt=(
            f"You are the API Auditor subagent. Project root: {WORK_DIR}\n\n"
            f"read_file: {WORK_DIR}/src/agent_interception/proxy/server.py\n"
            f"read_file: {WORK_DIR}/src/agent_interception/proxy/handler.py  max_lines=80\n"
            "search_code: pattern 'Route\\(|Mount\\(' in src/  file_glob='*.py'\n"
            "search_code: pattern 'X-Agent-Role|X-Interceptor' in src/\n\n"
            "Return a table METHOD | PATH | DESCRIPTION for every HTTP endpoint, "
            "plus a note on which special request headers the proxy reads."
        ),
    )

    # ── 5. Orchestrator dispatches test_runner ────────────────────────────────
    print("\n" + "─" * 60)
    print("  5/5a Orchestrator dispatches test_runner  [read_file]")
    print("─" * 60)
    await call_agent(
        orch_client, orch_sid, "orchestrator",
        tools=[READ_FILE],
        context=f"API audit findings: {api_out[:400]}",
        prompt=(
            "Acknowledge the API audit. "
            "State the task for the test_runner subagent in one sentence."
        ),
    )

    # ── 6. Test Runner runs pytest ────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  5/5b Test Runner  [run_command, read_file]")
    print("─" * 60)
    test_out = await call_agent(
        test_client, test_sid, "subagent",
        tools=[RUN_COMMAND, READ_FILE],
        prompt=(
            f"You are the Test Runner subagent. Project root: {WORK_DIR}\n\n"
            "run_command: 'uv run pytest tests/ --collect-only -q 2>&1 | tail -15'\n"
            "run_command: 'uv run pytest tests/ -q --tb=line 2>&1 | tail -25'\n"
            f"read_file:   {WORK_DIR}/tests/test_proxy/test_handler.py  max_lines=40\n\n"
            "Return: pass/fail status, total test count, per-file count, "
            "and one sentence on the testing approach."
        ),
    )

    # ── 7. Reporter writes CODEBASE_AUDIT.md ─────────────────────────────────
    print("\n" + "─" * 60)
    print("  5/5c Reporter  [read_file, write_file]")
    print("─" * 60)
    report_path = str(Path(WORK_DIR) / "CODEBASE_AUDIT.md")
    await call_agent(
        rep_client, rep_sid, "subagent",
        tools=[READ_FILE, WRITE_FILE],
        prompt=(
            f"You are the Reporter subagent. Write the audit report to {report_path}.\n\n"
            "Use write_file with this structure:\n\n"
            "# Codebase Health Audit\n\n"
            "## 1. Project Overview\n"
            f"{orch_out[:600]}\n\n"
            "## 2. Structure & Statistics\n"
            f"{scan_out[:600]}\n\n"
            "## 3. HTTP API Surface\n"
            f"{api_out[:600]}\n\n"
            "## 4. Test Suite\n"
            f"{test_out[:600]}\n\n"
            "## 5. Recommendations\n"
            "Write 4–5 concrete bullet-point recommendations based on the findings.\n\n"
            "Write the file now."
        ),
    )

    # ── 8. Orchestrator closes out ────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  Orchestrator closes the audit  [read_file]")
    print("─" * 60)
    await call_agent(
        orch_client, orch_sid, "orchestrator",
        tools=[READ_FILE],
        context="\n".join([
            f"Scanner:     {scan_out[:200]}",
            f"API Auditor: {api_out[:200]}",
            f"Test Runner: {test_out[:200]}",
        ]),
        prompt=(
            f"All subagents have completed. Use read_file to verify "
            f"{report_path} exists and has content. "
            "Then give a 2-sentence closing summary of the audit."
        ),
    )

    await asyncio.sleep(0.5)  # let proxy flush writes

    print(f"\n{'=' * 60}")
    print("  Audit complete.")
    print(f"  Report: {report_path}")
    print(f"\n  Multi-Agent tab:")
    print(f"    http://localhost:8080/_ui/  →  Multi-Agent")
    print(f"    Conversation ID: {conv_id}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
