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
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmark.analysis.metrics import load_trace, per_query_row
from benchmark.configs import CONFIGS
from benchmark.obs import Observer
from benchmark.tools import search as search_mod

# Probe targets: catch auth/SDK/network/model-availability issues for the next
# cell before committing the current cell's API budget. Both cells exercise the
# same Anthropic auth path, so probing both even from a Haiku-only run is
# strictly cheaper than discovering a broken Opus alias mid-Cell-2.
_PROBE_MODELS = ("claude-haiku-4-5-20251001", "claude-opus-4-7")


def _sanity_probe(models: tuple[str, ...]) -> bool:
    """Direct SDK call, 1-token max, outside the obs system. Prints per-model
    OK / FAIL and returns True iff every probe succeeded.
    """
    import anthropic

    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=0,
    )
    all_ok = True
    print("[probe] sanity-checking models before sweep")
    for m in models:
        try:
            client.messages.create(
                model=m,
                max_tokens=1,
                messages=[{"role": "user", "content": "ok"}],
            )
            print(f"[probe]   {m:38s} OK")
        except Exception as e:
            print(f"[probe]   {m:38s} FAIL  {type(e).__name__}: {e}")
            all_ok = False
    return all_ok


def _print_summary_table(out_dir: Path, label: str | None) -> None:
    """One-line per-cell summary read from the just-written trace JSONs."""
    rows = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            tf = load_trace(path)
        except Exception as e:
            print(f"[summary] WARN: could not load {path}: {e}")
            continue
        rows.append(per_query_row(tf))
    if not rows:
        print(f"[summary] no trace JSONs found under {out_dir}")
        return

    import statistics

    n = len(rows)
    median_active = statistics.median(r["active_latency_ms"] for r in rows)
    mean_llm_share = sum(r["llm_time_fraction"] for r in rows) / n
    mean_tool_share = sum(r["tool_time_fraction"] for r in rows) / n
    total_retry_waits = sum(r["num_retry_waits"] for r in rows)

    # Truncation count comes from the root span attribute set by the cell
    # configs (or by benchmark.backfill_termination on legacy traces).
    truncated_qids = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            blob = json.loads(path.read_text())
        except Exception:
            continue
        root = next((s for s in blob.get("spans", []) if s.get("parent_id") is None), None)
        if root is None:
            continue
        if bool(root.get("attrs", {}).get("agent.truncated", False)):
            truncated_qids.append(path.stem)
    n_truncated = len(truncated_qids)

    tag = label or out_dir.name
    print()
    print(f"[summary] {tag}")
    print("  " + "-" * 70)
    print(f"  queries completed         {n} / 20")
    print(f"  median active_latency_ms  {median_active:>10,.1f}")
    print(f"  mean LLM share of active  {mean_llm_share:>10.1%}")
    print(f"  mean tool share of active {mean_tool_share:>10.1%}")
    print(f"  total llm.retry_wait      {total_retry_waits:>10d}")
    print(f"  truncated (max_turns hit) {n_truncated:>10d}  {truncated_qids}")
    print("  " + "-" * 70)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        required=True,
        choices=[
            "py",
            "haiku_custom",
            "opus_custom",
            "pipeline_haiku",
            "pipeline_opus",
            "pipeline_haiku_hotpot",
            "chemcrow_py",
        ],
    )
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument(
        "--out",
        required=False,
        type=Path,
        default=None,
        help="Output directory. Falls back to module.DEFAULT_OUT_DIR if the "
             "selected config defines one.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Run first N queries (0 = all)")
    parser.add_argument("--only", type=str, default="", help="Run only the given query_id")
    parser.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="Sleep N seconds between queries (rate limiting). Default 5 s "
             "matches EVAL_PLAN.md pacing; flag remains for ad-hoc overrides.",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Skip the pre-sweep model sanity probes (haiku + opus). "
             "Default behavior is to probe both before committing 20 queries.",
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

    config_module = CONFIGS[args.config]

    out_dir = args.out or getattr(config_module, "DEFAULT_OUT_DIR", None)
    if out_dir is None:
        print(
            f"ERROR: --out is required for config {args.config!r} "
            "(no DEFAULT_OUT_DIR on module).",
            file=sys.stderr,
        )
        return 2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    label = getattr(config_module, "LABEL", None)
    print(
        f"[run] config={args.config}  label={label!r}  "
        f"queries={len(queries)}  out={out_dir}"
    )

    if not args.skip_probe:
        if not _sanity_probe(_PROBE_MODELS):
            print(
                "[run] sanity probe failed; aborting before committing the sweep. "
                "Pass --skip-probe to bypass.",
                file=sys.stderr,
            )
            return 2

    failed = 0
    consecutive_ddg_retry_queries = 0
    aborted = False
    # Per-query wall-time ceiling (seconds). A single query exceeding this is
    # treated as runaway behavior — abort the sweep so we do not burn budget
    # on stuck loops. Cell 1's slowest legitimate query was 117 s (q020,
    # truncated). 180 s leaves headroom for slower-but-recovering Opus turns.
    _PER_QUERY_WALL_LIMIT_S = 180.0
    for i, q in enumerate(queries, start=1):
        qid = q["query_id"]
        obs = Observer(
            config=args.config,
            query_id=qid,
            out_dir=out_dir,
            forward_to=args.forward_to or None,
            label=label,
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

        if dt > _PER_QUERY_WALL_LIMIT_S:
            print(
                f"[run] ABORT: {qid} took {dt:.1f}s wall (> {_PER_QUERY_WALL_LIMIT_S:.0f}s "
                f"per-query ceiling); stopping the sweep before more budget burns.",
                file=sys.stderr,
            )
            aborted = True
            break

        # Post-query guards: read the trace JSON we just wrote and decide
        # whether to keep going. The trace file is the source of truth even if
        # config_module.run() raised, because Observer flushes on root close.
        trace_path = out_dir / f"{qid}.json"
        if trace_path.exists():
            try:
                tf = load_trace(trace_path)
            except Exception:
                tf = None
            if tf is not None:
                num_retry_waits = sum(1 for s in tf.spans if s.name == "llm.retry_wait")
                if num_retry_waits > 3:
                    print(
                        f"[run] ABORT: {qid} accumulated {num_retry_waits} llm.retry_wait "
                        f"spans (> 3). Anthropic appears to be rate-limiting us hard; "
                        f"stopping before burning more credits.",
                        file=sys.stderr,
                    )
                    aborted = True
                    break
                ddg_retried = any(
                    s.name == "tool.search" and int(s.attrs.get("tool.retry_count", 0)) > 0
                    for s in tf.spans
                )
                if ddg_retried:
                    consecutive_ddg_retry_queries += 1
                else:
                    consecutive_ddg_retry_queries = 0
                if consecutive_ddg_retry_queries >= 3:
                    print(
                        f"[run] ABORT: DDG returned errors with retries on "
                        f"{consecutive_ddg_retry_queries} consecutive queries; stopping.",
                        file=sys.stderr,
                    )
                    aborted = True
                    break

        if args.sleep > 0 and i < len(queries):
            time.sleep(args.sleep)

    if aborted:
        print(f"[run] aborted early after {i} of {len(queries)} queries.")

    print(f"[run] done. {len(queries) - failed}/{len(queries)} succeeded, {failed} failed.")
    _print_summary_table(out_dir, label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
