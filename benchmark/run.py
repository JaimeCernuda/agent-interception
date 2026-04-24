"""Run a config over a set of queries and write one JSON trace per query.

Usage:
  uv run --group benchmark python -m benchmark.run \
    --config A --queries benchmark/queries/freshqa_20.json \
    --out benchmark/traces/A [--limit 1] [--only q001] [--sleep 1.0]

Env vars expected (see configs/*.py for details):
  Config A: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_PROVIDER_TAG
  Config B,C: ANTHROPIC_API_KEY (optional ANTHROPIC_MODEL)
  Search (shared): SEARCH_BACKEND=auto|google_cse|ddg|static  (default auto)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmark.configs import CONFIGS
from benchmark.obs import Observer
from benchmark.tools import search as search_mod


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, choices=["py"])
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0, help="Run first N queries (0 = all)")
    parser.add_argument("--only", type=str, default="", help="Run only the given query_id")
    parser.add_argument(
        "--sleep", type=float, default=0.0, help="Sleep N seconds between queries (rate limiting)"
    )
    parser.add_argument(
        "--forward-to",
        type=str,
        default="",
        help="Optional URL: POST each completed trace to this endpoint "
             "(e.g. http://localhost:8080/api/spans) for live analytics",
    )
    args = parser.parse_args()

    # Load benchmark/.env explicitly regardless of cwd.
    load_dotenv(Path(__file__).parent / ".env")

    queries_blob = json.loads(args.queries.read_text())
    queries = queries_blob["queries"]

    # Register static URLs for SEARCH_BACKEND=static
    static_map = {q["query_id"]: q.get("urls") or [] for q in queries}
    search_mod.register_static_urls(static_map)

    # Filter
    if args.only:
        queries = [q for q in queries if q["query_id"] == args.only]
    if args.limit > 0:
        queries = queries[: args.limit]

    if not queries:
        print(f"ERROR: no queries matched filter --only={args.only!r} --limit={args.limit}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    config_module = CONFIGS[args.config]

    print(f"[run] config={args.config}  queries={len(queries)}  out={args.out}")
    failed = 0
    for i, q in enumerate(queries, start=1):
        qid = q["query_id"]
        obs = Observer(
            config=args.config,
            query_id=qid,
            out_dir=args.out,
            forward_to=args.forward_to or None,
        )
        t0 = time.time()
        try:
            answer = config_module.run(q, obs)
            dt = time.time() - t0
            preview = (answer or "").replace("\n", " ")[:100]
            print(f"  [{i:>3}/{len(queries)}] {qid} ok ({dt:5.2f}s)  answer={preview!r}")
        except Exception as e:
            failed += 1
            dt = time.time() - t0
            print(f"  [{i:>3}/{len(queries)}] {qid} FAIL ({dt:5.2f}s)  {type(e).__name__}: {e}")
        if args.sleep > 0 and i < len(queries):
            time.sleep(args.sleep)

    print(f"[run] done. {len(queries) - failed}/{len(queries)} succeeded, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
