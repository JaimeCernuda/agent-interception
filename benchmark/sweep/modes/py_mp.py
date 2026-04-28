"""Python multi-process harness: subprocess.Popen per query, semaphore-bounded.

This is INTENTIONALLY not a long-lived ProcessPoolExecutor. We mirror Raj et al.'s
`&` shell-background pattern: each query is a fresh `python -m
benchmark.configs.config_chemcrow_py --query-id ... --output ...` process that
imports RDKit + claude-agent-sdk from scratch, runs ONE query, exits. The
N-bound is enforced by `threading.Semaphore`, not by a worker pool.

If you reach for `ProcessPoolExecutor(max_workers=N, max_tasks_per_child=1)`:
read the comment at the top of this file and the spec — that recycles workers
but the pool is still long-lived and amortizes import cost across the queue.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue

from benchmark.sweep.summary import BatchResult


def _run_one(
    query: dict,
    out_dir: Path,
    sem: threading.Semaphore,
    config_name: str,
    queries_path: Path,
    failures: Queue,
) -> None:
    """Per-query worker: take the semaphore, fork a fresh Python, wait."""
    qid = query["query_id"]
    with sem:
        # PYTHONUNBUFFERED so the subprocess's stdout/stderr is visible without
        # waiting for buffer flush; useful when a query stalls.
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        cmd = [
            sys.executable,
            "-m",
            "benchmark.configs.config_chemcrow_py",
            "--query-id", qid,
            "--queries", str(queries_path),
            "--output", str(out_dir),
            "--config", config_name,
        ]
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                print(f"[py-mp] {qid} FAIL rc={proc.returncode}; stderr_tail:\n"
                      f"{(proc.stderr or '')[-400:]}")
                failures.put(qid)
        except Exception as e:
            print(f"[py-mp] {qid} FAIL: {type(e).__name__}: {e}")
            failures.put(qid)


def run_batch(queries: list[dict], out_dir: Path, concurrency: int, *,
              config_name: str = "chemcrow_py_mp",
              queries_path: Path = Path("benchmark/queries/chemcrow_20.json")) -> BatchResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = threading.Semaphore(concurrency)
    failures: Queue = Queue()
    threads = [
        threading.Thread(
            target=_run_one,
            args=(q, out_dir, sem, config_name, queries_path, failures),
            name=f"py-mp:{q['query_id']}",
        )
        for q in queries
    ]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_ms = (time.monotonic() - t0) * 1000.0

    failure_count = failures.qsize()
    trace_paths = [out_dir / f"{q['query_id']}.json" for q in queries]
    trace_paths = [p for p in trace_paths if p.exists()]
    return BatchResult(wallclock_ms=wall_ms, trace_paths=trace_paths, num_failures=failure_count)
