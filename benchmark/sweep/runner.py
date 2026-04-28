"""Concurrency-sweep runner. One invocation = one (mode, N) point.

Usage:
  python -m benchmark.sweep.runner --mode py-mt --concurrency 8 \\
      --runs 3 --warmup-queries 4 --queries benchmark/queries/chemcrow_20.json

Writes (under --out-root, default benchmark/output/sweep):
    <mode>_n<N>/
        traces/<run_index>/<query_id>.json
        cold_start.json
        summary.json
        cli_args.json
    sweep_index.json   (top-level pointer; updated incrementally)

Sweep multiple Ns in one go:
  python -m benchmark.sweep.runner --mode py-mt --sweep --runs 1
  # runs N ∈ {1, 2, 4, 8, 16, 32, 64} sequentially.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmark.sweep.modes import HARNESSES, PRO_PLAN_MODES
from benchmark.sweep.summary import build_summary, update_index, write_point

DEFAULT_NS = (1, 2, 4, 8, 16, 32, 64)
EXTENDED_NS = (1, 2, 4, 8, 16, 32, 64, 128)
DEFAULT_QUERIES = Path("benchmark/queries/chemcrow_20.json")
DEFAULT_OUT_ROOT = Path("benchmark/output/sweep")


def _load_queries(path: Path) -> list[dict]:
    return json.loads(path.read_text())["queries"]


def _warmup_subset(all_queries: list[dict], n_warmup: int) -> list[dict]:
    """Pick warmup queries: equal numbers of medium and heavy, capped at n_warmup."""
    if n_warmup <= 0:
        return []
    medium = [q for q in all_queries if q.get("label") == "medium"]
    heavy = [q for q in all_queries if q.get("label") == "heavy"]
    half = max(1, n_warmup // 2)
    pick = medium[:half] + heavy[:half]
    return pick[:n_warmup]


def _cap_concurrency_for_pympp(mode: str, concurrency: int) -> tuple[int, str | None]:
    """Phase-2 spec: py-mp N capped at cpu_count*2; warn but proceed if exceeded."""
    if mode != "py-mp":
        return concurrency, None
    cap = (os.cpu_count() or 1) * 2
    if concurrency > cap:
        return concurrency, (
            f"WARNING: py-mp N={concurrency} exceeds cpu_count*2={cap}; "
            "this is in Raj et al.'s 'broken' regime. Proceeding."
        )
    return concurrency, None


def _llm_transport(mode: str) -> str:
    return "claude-cli" if mode in PRO_PLAN_MODES else "anthropic-rest"


def run_point(
    *,
    mode: str,
    concurrency: int,
    runs: int,
    warmup_queries: int,
    queries_path: Path,
    out_root: Path,
    skip_cold_start: bool = False,
    cli_args: dict | None = None,
) -> Path:
    """Run one (mode, N) point: cold start + warmup + measured runs.

    Returns the point's output directory.
    """
    if mode not in HARNESSES:
        raise SystemExit(f"unknown --mode {mode!r}; expected one of {sorted(HARNESSES)}")

    concurrency, cap_msg = _cap_concurrency_for_pympp(mode, concurrency)
    if cap_msg:
        print(cap_msg, file=sys.stderr)

    queries = _load_queries(queries_path)
    point_dir = out_root / f"{mode}_n{concurrency}"
    traces_root = point_dir / "traces"
    traces_root.mkdir(parents=True, exist_ok=True)

    if warmup_queries == 0:
        print(
            "WARNING: warmup disabled, N=1 measurements will be cold-start dominated",
            file=sys.stderr,
        )

    # 1. Cold start probe ------------------------------------------------------
    cold_trace_path: Path | None = None
    if not skip_cold_start:
        print(f"[sweep] cold-start probe ({mode}, N=1, 1 query, fresh process)")
        cold_dir = traces_root / "cold"
        cold_dir.mkdir(parents=True, exist_ok=True)
        # Cold start runs at N=1 explicitly to measure first-query overhead;
        # uses the same harness so the path is identical to a measured run.
        harness = HARNESSES[mode]
        cold_q = queries[:1]
        cold_result = harness(cold_q, cold_dir, 1)
        cold_start_ms = cold_result.wallclock_ms
        if cold_result.trace_paths:
            cold_trace_path = cold_result.trace_paths[0]
        print(f"[sweep]   cold_start_ms = {cold_start_ms:.1f}")
    else:
        cold_start_ms = 0.0

    # 2. Warmup (timing discarded) --------------------------------------------
    if warmup_queries > 0:
        warm_q = _warmup_subset(queries, warmup_queries)
        print(f"[sweep] warmup: {len(warm_q)} queries at N={concurrency}")
        warm_dir = traces_root / "warmup"
        warm_dir.mkdir(parents=True, exist_ok=True)
        harness = HARNESSES[mode]
        _ = harness(warm_q, warm_dir, concurrency)

    # 3. Measured runs ---------------------------------------------------------
    run_results = []
    for i in range(runs):
        run_dir = traces_root / f"run{i}"
        run_dir.mkdir(parents=True, exist_ok=True)
        print(f"[sweep] measured run {i + 1}/{runs} (mode={mode}, N={concurrency}, "
              f"queries={len(queries)})")
        harness = HARNESSES[mode]
        result = harness(queries, run_dir, concurrency)
        run_results.append(result)
        print(f"[sweep]   wallclock_ms={result.wallclock_ms:.1f} "
              f"failures={result.num_failures}/{len(queries)}")

    # 4. Aggregate ------------------------------------------------------------
    summary = build_summary(
        mode=mode,
        concurrency=concurrency,
        cold_start_ms=cold_start_ms,
        warmup_queries=warmup_queries,
        runs=run_results,
        llm_transport=_llm_transport(mode),
        cli_args=cli_args or {},
    )
    write_point(point_dir, summary, cold_trace_path, cli_args or {})
    update_index(out_root, mode, concurrency, point_dir)
    print(f"[sweep] wrote {point_dir / 'summary.json'}")
    return point_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=sorted(HARNESSES))
    parser.add_argument("--concurrency", type=int, default=1,
                        help="N (concurrency level). Ignored if --sweep is set.")
    parser.add_argument("--sweep", action="store_true",
                        help="run all default Ns sequentially")
    parser.add_argument("--max-n", type=int, default=64,
                        help="Cap N when --sweep is set. Default 64; pass 128 to opt in.")
    parser.add_argument("--runs", type=int, default=3,
                        help="number of measured repetitions per (mode, N)")
    parser.add_argument("--warmup-queries", type=int, default=4,
                        help="warmup query count (default 4: 2 medium + 2 heavy). "
                             "0 disables warmup.")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES,
                        help="path to the queries JSON")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT,
                        help="output root; one subdir per (mode, N)")
    parser.add_argument("--skip-cold-start", action="store_true",
                        help="(unit tests only) skip the cold-start probe")
    parser.add_argument("--reset", action="store_true",
                        help="rm -r each <mode>_n<N> dir before running")
    args = parser.parse_args(argv)

    load_dotenv(Path("benchmark/.env"))

    cli_args = vars(args).copy()
    cli_args["queries"] = str(args.queries)
    cli_args["out_root"] = str(args.out_root)

    if args.sweep:
        ns = [n for n in (EXTENDED_NS if args.max_n >= 128 else DEFAULT_NS) if n <= args.max_n]
    else:
        ns = [args.concurrency]

    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"[sweep] mode={args.mode} Ns={ns} runs={args.runs} "
          f"warmup={args.warmup_queries} out={args.out_root}")
    started = time.time()
    for n in ns:
        if args.reset:
            point_dir = args.out_root / f"{args.mode}_n{n}"
            if point_dir.exists():
                print(f"[sweep] --reset: rm -r {point_dir}")
                shutil.rmtree(point_dir)
        run_point(
            mode=args.mode,
            concurrency=n,
            runs=args.runs,
            warmup_queries=args.warmup_queries,
            queries_path=args.queries,
            out_root=args.out_root,
            skip_cold_start=args.skip_cold_start,
            cli_args=cli_args,
        )
    print(f"[sweep] done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
