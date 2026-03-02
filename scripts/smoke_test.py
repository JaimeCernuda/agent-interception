"""Smoke test — verifies the proxy + claude CLI pipeline end-to-end.

Sends a single minimal prompt (no tools, no file access) and checks:
  1. Proxy is reachable
  2. Claude CLI can talk to the API through the proxy
  3. The interaction is captured and stored
  4. The session appears in the proxy's session list

Run with: uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from _common import CLI_PATH, PROXY_URL, banner, start_session
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk._errors import MessageParseError

OK   = "  ✓"
FAIL = "  ✗"


async def check_proxy_health() -> bool:
    print("1. Proxy health check...")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{PROXY_URL}/_interceptor/health")
            if r.status_code == 200:
                print(f"{OK} Proxy is up at {PROXY_URL}")
                return True
            print(f"{FAIL} Proxy returned {r.status_code}")
            return False
    except Exception as e:
        print(f"{FAIL} Cannot reach proxy: {e}")
        print("     → Start it with: uv run agent-interceptor start")
        return False


def check_cli() -> bool:
    print("2. Claude CLI check...")
    if CLI_PATH:
        print(f"{OK} CLI found at {CLI_PATH}")
        return True
    print(f"{FAIL} 'claude' not found in PATH")
    print("     → Install Claude Code: https://claude.ai/download")
    return False


async def run_agent(session_id: str) -> str | None:
    print("3. Sending prompt through proxy...")
    result_text: str | None = None
    try:
        async for msg in query(
            prompt="Reply with exactly: PROXY_OK",
            options=ClaudeAgentOptions(
                cli_path=CLI_PATH,
                allowed_tools=[],          # no tools — fastest possible call
                permission_mode="bypassPermissions",
                max_turns=1,
            ),
        ):
            if hasattr(msg, "result") and msg.result:
                result_text = msg.result
    except MessageParseError as e:
        if "Unknown message type" in str(e):
            pass  # rate_limit_event etc — not a real failure
        else:
            print(f"{FAIL} SDK parse error: {e}")
            return None
    except Exception as e:
        print(f"{FAIL} Agent error: {e}")
        return None

    if result_text:
        preview = result_text.strip()[:80]
        print(f"{OK} Got response: {preview!r}")
    else:
        print(f"{FAIL} No result returned")
    return result_text


async def check_captured(session_id: str) -> bool:
    print("4. Checking interaction was captured...")
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(
            f"{PROXY_URL}/_interceptor/interactions",
            params={"session_id": session_id, "limit": 5},
        )
        interactions = r.json()
        if interactions:
            count = len(interactions)
            model = interactions[0].get("model") or "unknown"
            status = interactions[0].get("status_code")
            latency = interactions[0].get("total_latency_ms")
            lat_str = f"{latency:.0f}ms" if latency else "?"
            print(
                f"{OK} {count} interaction(s) captured — "
                f"model={model}, status={status}, latency={lat_str}"
            )
            return True
        print(f"{FAIL} No interactions found for session {session_id!r}")
        return False


async def check_session_in_list(session_id: str) -> bool:
    print("5. Checking session appears in session list...")
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{PROXY_URL}/api/sessions")
        sessions = r.json()
        ids = [s.get("sessionId") for s in sessions]
        if session_id in ids:
            print(f"{OK} Session {session_id!r} visible in dashboard")
            return True
        print(f"{FAIL} Session not found in list (got {ids})")
        return False


async def main() -> None:
    session_id = start_session("smoke-test")
    banner("Smoke Test — end-to-end pipeline check", session_id)

    results: list[bool] = []

    proxy_ok = await check_proxy_health()
    results.append(proxy_ok)

    cli_ok = check_cli()
    results.append(cli_ok)

    if proxy_ok and cli_ok:
        response = await run_agent(session_id)
        results.append(response is not None)

        if response is not None:
            await asyncio.sleep(0.5)  # let the proxy finish storing
            results.append(await check_captured(session_id))
            results.append(await check_session_in_list(session_id))
    else:
        print("   (skipping agent + capture checks — fix above first)")

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    if passed == total:
        print(f"  All {total} checks passed. Pipeline is healthy.")
        print(f"  Dashboard: {PROXY_URL}/_ui/  →  Visualize  →  {session_id}")
    else:
        print(f"  {passed}/{total} checks passed.")
    print(f"{'=' * 60}\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
