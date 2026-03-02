"""Verify the interceptor captured everything correctly.

Run this AFTER running one or more of the other scripts to check the
interceptor DB has complete, well-formed entries and export them to a
readable JSON file.

Run with: uv run python scripts/verify_logs.py [--db interceptor.db]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

PROXY = "http://127.0.0.1:8080"


async def main() -> None:
    print("=" * 60)
    print("  Interceptor Log Verification")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # --- Health check ---
        try:
            resp = await client.get(f"{PROXY}/_interceptor/health", timeout=3)
            resp.raise_for_status()
        except Exception as e:
            print(f"\n  ERROR: Proxy not reachable at {PROXY} — {e}")
            print("  Start it first:  uv run agent-interceptor start")
            sys.exit(1)

        # --- Stats ---
        stats = (await client.get(f"{PROXY}/_interceptor/stats")).json()
        total = stats["total_interactions"]
        print(f"\n  Total interactions: {total}")
        print(f"  By provider: {stats['by_provider']}")
        print(f"  By model:    {stats['by_model']}")
        if stats.get("avg_latency_ms"):
            print(f"  Avg latency: {stats['avg_latency_ms']:.0f} ms")

        if total == 0:
            print("\n  No interactions to verify. Run a script first.")
            sys.exit(0)

        # --- Fetch all interactions ---
        summaries = (
            await client.get(f"{PROXY}/_interceptor/interactions", params={"limit": 500})
        ).json()
        print(f"\n  Fetched {len(summaries)} interaction summaries")

        streaming = sum(1 for s in summaries if s["is_streaming"])
        print(f"  Streaming: {streaming}  Non-streaming: {len(summaries) - streaming}")

        errors_4xx = [s for s in summaries if s["status_code"] and s["status_code"] >= 400]
        print(f"  4xx/5xx responses: {len(errors_4xx)}")

        missing = [s for s in summaries if s["status_code"] is None]
        print(f"  Missing status (proxy failure): {len(missing)}")

        # --- Fetch full details + validate ---
        print("\n  Fetching full interaction details...")
        full_entries: list[dict] = []
        issues: list[str] = []

        for s in summaries:
            r = await client.get(f"{PROXY}/_interceptor/interactions/{s['id']}")
            if r.status_code != 200:
                issues.append(f"{s['id']}: HTTP {r.status_code} fetching detail")
                continue
            entry = r.json()
            full_entries.append(entry)

            # Validate required fields
            if not entry.get("id"):
                issues.append("Entry missing id")
            if not entry.get("timestamp"):
                issues.append(f"{entry.get('id', '?')}: missing timestamp")
            if not entry.get("provider"):
                issues.append(f"{entry.get('id', '?')}: missing provider")
            if entry.get("status_code") is None and not entry.get("error"):
                issues.append(f"{entry.get('id', '?')}: no status_code and no error")

            # Streaming entries should have chunks
            if entry.get("is_streaming") and not entry.get("stream_chunks"):
                issues.append(f"{entry.get('id', '?')}: streaming but no chunks")

        # --- Field coverage ---
        def count(field: str) -> int:
            return sum(1 for e in full_entries if e.get(field))

        print(f"\n  Field coverage ({len(full_entries)} entries):")
        for f in [
            "system_prompt",
            "messages",
            "tools",
            "tool_calls",
            "response_text",
            "stream_chunks",
            "token_usage",
            "cost_estimate",
            "total_latency_ms",
        ]:
            n = count(f)
            pct = n / len(full_entries) * 100 if full_entries else 0
            print(f"    {f:24s}  {n:4d} / {len(full_entries)}  ({pct:.0f}%)")

        # --- Issues ---
        if issues:
            print(f"\n  ISSUES ({len(issues)}):")
            for issue in issues[:20]:
                print(f"    - {issue}")
            if len(issues) > 20:
                print(f"    ... and {len(issues) - 20} more")
        else:
            print("\n  All entries valid — no issues found")

        # --- Export ---
        out_path = Path("interceptor_log.json")
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(full_entries, fp, indent=2, ensure_ascii=False)
        size_kb = out_path.stat().st_size / 1024
        print(f"\n  Exported {len(full_entries)} interactions to {out_path} ({size_kb:.0f} KB)")

    print(f"\n{'=' * 60}")
    print("  Done")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
