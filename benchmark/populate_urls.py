"""One-shot URL population for freshqa_20.json via DDG.

Run once, commit the resulting queries JSON. The benchmark itself uses
SEARCH_BACKEND=static thereafter, so runs are deterministic.

Usage:
  PYTHONPATH=. uv run --group benchmark python benchmark/populate_urls.py [--force]

Skips queries that already have urls[] unless --force is passed. Also clears
URLs that currently 404/403 on a HEAD check (q001 had stale Britannica links).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from ddgs import DDGS

QUERIES = Path("benchmark/queries/freshqa_20.json")
PER_QUERY_URLS = 4  # fetch 4, agent will use top 2
DDG_MAX_RESULTS = 6  # pull a few extra so we can drop the bad ones
SLEEP_BETWEEN = 3.0
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def reachable(url: str) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=6.0, headers={"User-Agent": UA}) as c:
            r = c.head(url)
            if r.status_code >= 400:
                # Some sites 405 HEAD but allow GET; try a tiny GET.
                r = c.get(url)
            return 200 <= r.status_code < 400
    except Exception:
        return False


def ddg_urls(query: str) -> list[str]:
    for attempt in range(3):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=DDG_MAX_RESULTS))
            urls = [r.get("href") or r.get("url") for r in results]
            urls = [u for u in urls if u and u.startswith("http")]
            if urls:
                return urls
        except Exception as e:
            print(f"    DDG attempt {attempt + 1} failed: {e}")
        time.sleep(5.0 * (attempt + 1))
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-populate even if urls[] present")
    ap.add_argument("--revalidate", action="store_true",
                    help="drop existing URLs that fail a HEAD check before re-populating")
    args = ap.parse_args()

    data = json.loads(QUERIES.read_text())
    queries = data["queries"]

    if args.revalidate:
        for q in queries:
            urls = q.get("urls") or []
            kept = [u for u in urls if reachable(u)]
            if len(kept) != len(urls):
                print(f"  {q['query_id']} revalidate: {len(urls)} -> {len(kept)}")
                q["urls"] = kept

    to_populate = [q for q in queries if args.force or not q.get("urls")]
    print(f"populating {len(to_populate)} queries via DDG")

    populated = 0
    for i, q in enumerate(to_populate, start=1):
        qid = q["query_id"]
        question = q["question"]
        print(f"[{i:>2}/{len(to_populate)}] {qid}: {question[:70]}")
        urls = ddg_urls(question)
        if not urls:
            print("    -> no results")
            continue
        # Reachability check, keep first PER_QUERY_URLS that are alive.
        alive: list[str] = []
        for u in urls:
            if reachable(u):
                alive.append(u)
            else:
                print(f"    skip (dead): {u[:80]}")
            if len(alive) >= PER_QUERY_URLS:
                break
        if alive:
            q["urls"] = alive
            populated += 1
            print(f"    -> {len(alive)} urls")
        else:
            print("    -> none reachable")
        time.sleep(SLEEP_BETWEEN)

    QUERIES.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\npopulated {populated}/{len(to_populate)} queries; wrote {QUERIES}")

    # Summary of final state
    still_empty = [q["query_id"] for q in queries if not q.get("urls")]
    if still_empty:
        print(f"still empty ({len(still_empty)}): {still_empty}")


if __name__ == "__main__":
    main()
