"""SWE-Agent batch driver: 20 queries × concurrency N → 20 trace files.

Mirrors benchmark/run_toolformer_batch.py but adds the per-query workspace copy
that SWE-Agent needs (each agent mutates files in the workspace, so each query
must operate on a fresh copy of the canonical fixture).

Usage:
  uv run python -m benchmark.run_sweagent_batch \\
    --queries benchmark/queries/sweagent_20.json \\
    --concurrency 8 \\
    --out benchmark/output/sweagent_diag_n8

Trace files are named {query_id}.json (no `_r0` suffix). The Phase-2 sweep
wrapper reads them by iterating the queries list and looking for the matching
filename.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmark.configs.config_sweagent_py import run as run_py
from benchmark.obs import Observer


def _run_one(q: dict, out_dir: Path, config_name: str,
             workspace_root: Path, workspace_copy_root: Path) -> tuple[str, str | None, float]:
    src_ws = (workspace_root / q["workspace_dir"]).resolve()
    if not src_ws.exists():
        return q["query_id"], f"workspace missing: {src_ws}", 0.0
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
        run_py(q, obs, run_ws)
    except Exception as e:
        return q["query_id"], repr(e), time.perf_counter() - t0
    return q["query_id"], None, time.perf_counter() - t0


async def _async_main(queries: list[dict], out_dir: Path, config_name: str,
                      workspace_root: Path, concurrency: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_copy_root = out_dir / "workspaces"
    workspace_copy_root.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[str, str | None, float]] = []

    async def _wrap(q: dict):
        async with sem:
            return await asyncio.to_thread(
                _run_one, q, out_dir, config_name, workspace_root, workspace_copy_root,
            )

    print(f"[batch] {len(queries)} queries  concurrency={concurrency}  out={out_dir}")
    wall_start = time.time()
    futs = [_wrap(q) for q in queries]
    for fut in asyncio.as_completed(futs):
        qid, err, dt = await fut
        results.append((qid, err, dt))
        flag = "FAIL" if err else "OK"
        suffix = f" {err}" if err else ""
        print(f"  [{flag}] {qid} ({dt:.2f}s){suffix}")
    wall_total = time.time() - wall_start

    n_ok = sum(1 for _, e, _ in results if e is None)
    print(f"\n[batch] wallclock={wall_total:.2f}s")
    print(f"[batch] succeeded: {n_ok}/{len(results)}")
    if n_ok < len(results):
        print("[batch] failures:")
        for qid, err, _dt in results:
            if err is not None:
                print(f"   {qid}: {err}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path,
                        default=Path("benchmark/queries/sweagent_20.json"))
    parser.add_argument("--out", type=Path,
                        default=Path("benchmark/output/sweagent_batch"))
    parser.add_argument("--config", default="sweagent_py")
    parser.add_argument("--workspace-root", type=Path,
                        default=Path("benchmark/queries"))
    parser.add_argument("--query-ids", default="",
                        help="comma-separated subset of query_ids; empty = all")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    load_dotenv(Path("benchmark/.env"))
    blob = json.loads(args.queries.read_text())
    queries = blob["queries"]
    if args.query_ids:
        wanted = {s.strip() for s in args.query_ids.split(",") if s.strip()}
        queries = [q for q in queries if q["query_id"] in wanted]
        if not queries:
            print(f"no matching query_ids in {wanted}", file=sys.stderr)
            return 2

    return asyncio.run(_async_main(queries, args.out, args.config,
                                    args.workspace_root, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
