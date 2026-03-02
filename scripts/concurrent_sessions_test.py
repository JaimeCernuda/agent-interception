"""Concurrent session stress test.

Launches N Claude sessions truly simultaneously (separate subprocesses),
each with its own ANTHROPIC_BASE_URL pointing to a unique session prefix.

After all finish, queries the proxy to verify:
  - Each session appears in the session list
  - Interactions are correctly isolated per session
  - No cross-session contamination
  - No DB errors (locked, corrupted, etc.)

Run with: uv run python scripts/concurrent_sessions_test.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from dataclasses import dataclass

import httpx

PROXY_URL = os.environ.get("INTERCEPTOR_URL", "http://127.0.0.1:8080")
CLI_PATH: str | None = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")

# How many parallel sessions to fire
N_SESSIONS = 3

OK   = "  ✓"
FAIL = "  ✗"


@dataclass
class SessionResult:
    session_id: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


async def run_session(session_id: str, prompt: str) -> SessionResult:
    """Run a single `claude -p` call as a subprocess with its own env."""
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"{PROXY_URL}/_session/{session_id}"
    env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")

    cli = CLI_PATH or "claude"
    t0 = asyncio.get_event_loop().time()

    proc = await asyncio.create_subprocess_exec(
        cli, "-p", prompt,
        "--allowedTools", "none",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    elapsed = asyncio.get_event_loop().time() - t0

    return SessionResult(
        session_id=session_id,
        returncode=proc.returncode or 0,
        stdout=stdout_b.decode(errors="replace").strip(),
        stderr=stderr_b.decode(errors="replace").strip(),
        elapsed=elapsed,
    )


async def check_proxy_health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{PROXY_URL}/_interceptor/health")
            return r.status_code == 200
    except Exception:
        return False


async def verify_sessions(session_ids: list[str]) -> tuple[int, int]:
    """Return (passed, total) verification checks."""
    passed = 0
    total = 0

    async with httpx.AsyncClient(timeout=10) as client:
        # Check all sessions appear in the session list
        r = await client.get(f"{PROXY_URL}/api/sessions")
        all_sessions = {s["sessionId"] for s in r.json()}

        for sid in session_ids:
            total += 1
            if sid in all_sessions:
                print(f"{OK} Session {sid!r} present in list")
                passed += 1
            else:
                print(f"{FAIL} Session {sid!r} MISSING from list")

        # Check each session has at least one interaction
        for sid in session_ids:
            total += 1
            r = await client.get(
                f"{PROXY_URL}/_interceptor/interactions",
                params={"session_id": sid, "limit": 10},
            )
            interactions = r.json()
            if interactions:
                count = len(interactions)
                # Verify no interaction belongs to a different session
                wrong = [i for i in interactions if i.get("session_id") != sid]
                if wrong:
                    print(
                        f"{FAIL} Session {sid!r}: {len(wrong)} cross-session interaction(s) found!"
                    )
                else:
                    print(f"{OK} Session {sid!r}: {count} interaction(s), all correctly tagged")
                    passed += 1
            else:
                print(f"{FAIL} Session {sid!r}: 0 interactions captured")

        # DB integrity check via stats endpoint (proxy would crash/error if DB is corrupt)
        total += 1
        try:
            r = await client.get(f"{PROXY_URL}/_interceptor/stats")
            r.raise_for_status()
            total_count = r.json().get("total_interactions")
            print(f"{OK} DB stats endpoint healthy (total interactions: {total_count})")
            passed += 1
        except Exception as e:
            print(f"{FAIL} DB stats endpoint error: {e}")

    return passed, total


async def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"  Concurrent Sessions Test — {N_SESSIONS} simultaneous agents")
    print(f"  Proxy: {PROXY_URL}")
    print(f"  CLI:   {CLI_PATH or 'not found'}")
    print(f"{'=' * 60}\n")

    # Pre-flight checks
    if not await check_proxy_health():
        print(f"{FAIL} Proxy not reachable at {PROXY_URL}")
        print("     → Start it with: uv run agent-interceptor start")
        sys.exit(1)
    print(f"{OK} Proxy is up")

    if not CLI_PATH:
        print(f"{FAIL} 'claude' CLI not found in PATH")
        sys.exit(1)
    print(f"{OK} CLI found\n")

    # Generate unique session IDs
    sessions = [
        (f"concurrent-{uuid.uuid4().hex[:6]}", f"Reply with exactly: SESSION_{i+1}_OK")
        for i in range(N_SESSIONS)
    ]

    print(f"Launching {N_SESSIONS} sessions simultaneously...")
    for sid, _ in sessions:
        print(f"  → {sid}")
    print()

    # Fire all sessions at exactly the same time
    t_start = asyncio.get_event_loop().time()
    results: list[SessionResult] = await asyncio.gather(
        *[run_session(sid, prompt) for sid, prompt in sessions]
    )
    t_total = asyncio.get_event_loop().time() - t_start

    # Print per-session results
    print("\nAgent results:")
    all_ok = True
    for r in results:
        status = OK if r.returncode == 0 else FAIL
        print(f"{status} {r.session_id}  [{r.elapsed:.1f}s]  rc={r.returncode}")
        if r.returncode != 0 and r.stderr:
            print(f"     stderr: {r.stderr[:200]}")
        all_ok = all_ok and (r.returncode == 0)

    print(f"\n  All agents finished in {t_total:.1f}s total\n")

    # Give the proxy a moment to flush any pending writes
    await asyncio.sleep(1.0)

    # Verify isolation and correctness
    print("Verifying captured data...")
    session_ids = [sid for sid, _ in sessions]
    passed, total = await verify_sessions(session_ids)

    print(f"\n{'=' * 60}")
    if passed == total:
        print(f"  All {total} checks passed. No concurrency bugs detected.")
    else:
        print(f"  {passed}/{total} checks passed. Review failures above.")
    print(f"{'=' * 60}\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
