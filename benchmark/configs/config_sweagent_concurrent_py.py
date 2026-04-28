"""SWE-Agent Python concurrent driver.

Runs N copies of `config_sweagent_py.run` in parallel via ThreadPoolExecutor,
all inside a single Python process. ThreadPool (not ProcessPool) is the
right choice for the GIL hypothesis: per-thread CPU work in the agent loop
contends on the GIL.

Each thread calls `config_sweagent_py.run(...)` which internally does
`asyncio.run(...)`. Threads each have their own event loop; the loops don't
share state.

Run:
    CONCURRENT_BATCH_SIZE=8 uv run --group benchmark python \\
        -m benchmark.configs.config_sweagent_concurrent_py \\
        --queries benchmark/queries/sweagent_20.json --query-ids q01,q02,q03
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from benchmark.configs.config_sweagent_py import run as sweagent_run
from benchmark.obs import Observer

DEFAULT_OUT_DIR = Path("benchmark/output/sweagent_concurrent_py")


def _build_query_list(queries: list[dict], ids: list[str], replicate: int) -> list[dict]:
    """Pick the requested query ids and replicate each `replicate` times."""
    by_id = {q["query_id"]: q for q in queries}
    out: list[dict] = []
    for qid in ids:
        if qid not in by_id:
            raise SystemExit(f"--query-ids: unknown id {qid!r}")
        q = by_id[qid]
        for r in range(replicate):
            replica = dict(q)
            replica["query_id"] = f"{qid}_r{r}"
            out.append(replica)
    return out


def _run_one(
    q: dict,
    config_name: str,
    out_dir: Path,
    workspace_root: Path,
    workspace_copy_root: Path,
) -> tuple[str, float, str | None]:
    """Worker: run one query, return (query_id, wall_seconds, err)."""
    src_ws = (workspace_root / q["workspace_dir"]).resolve()
    if not src_ws.exists():
        return q["query_id"], 0.0, f"workspace missing: {src_ws}"
    run_ws = workspace_copy_root / q["query_id"]
    if run_ws.exists():
        shutil.rmtree(run_ws)
    shutil.copytree(src_ws, run_ws)

    obs = Observer(
        config=config_name,
        query_id=q["query_id"],
        out_dir=out_dir,
        label=q.get("label"),
    )
    t0 = time.perf_counter()
    try:
        sweagent_run(q, obs, run_ws)
    except Exception as e:
        return q["query_id"], time.perf_counter() - t0, repr(e)
    return q["query_id"], time.perf_counter() - t0, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("benchmark/queries/sweagent_20.json"),
    )
    parser.add_argument(
        "--query-ids",
        default="q01,q02,q03",
        help="comma-separated query ids to include (default: q01,q02,q03)",
    )
    parser.add_argument(
        "--replicate",
        type=int,
        default=None,
        help="run each picked query this many times (default: equals --concurrency)",
    )
    parser.add_argument("--concurrency", type=int,
                        default=int(os.environ.get("CONCURRENT_BATCH_SIZE", "1")))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--config", default="sweagent_concurrent_py")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("benchmark/queries"),
    )
    args = parser.parse_args()

    load_dotenv(Path("benchmark/.env"))

    blob = json.loads(args.queries.read_text())
    queries = blob["queries"]
    qids = [s.strip() for s in args.query_ids.split(",") if s.strip()]
    replicate = args.replicate if args.replicate is not None else max(1, args.concurrency)
    work = _build_query_list(queries, qids, replicate)

    args.out.mkdir(parents=True, exist_ok=True)
    workspace_copy_root = args.out / "workspaces"
    workspace_copy_root.mkdir(parents=True, exist_ok=True)

    print(
        f"[run] config={args.config} pickedIDs={qids} replicate={replicate} "
        f"total={len(work)} concurrency={args.concurrency} out={args.out}",
        file=sys.stderr,
    )

    wall_start = time.perf_counter()
    results: list[tuple[str, float, str | None]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [
            ex.submit(_run_one, q, args.config, args.out, args.workspace_root, workspace_copy_root)
            for q in work
        ]
        for fut in as_completed(futures):
            qid, dt, err = fut.result()
            results.append((qid, dt, err))
            tag = "FAIL" if err else "ok"
            print(f"  [{len(results)}/{len(work)}] {qid} {tag} ({dt:.2f}s) {err or ''}", file=sys.stderr)
    wallclock_s = time.perf_counter() - wall_start

    durations = [r[1] for r in results if r[2] is None]
    failed = sum(1 for r in results if r[2] is not None)
    if durations:
        print(
            f"\nWALLCLOCK_S={wallclock_s:.3f} med={statistics.median(durations):.2f}s "
            f"p95={sorted(durations)[max(0, int(len(durations)*0.95)-1)]:.2f}s "
            f"failed={failed}/{len(results)}",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
