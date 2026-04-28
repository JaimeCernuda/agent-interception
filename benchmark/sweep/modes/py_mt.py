"""Python multi-thread harness: long-lived ThreadPoolExecutor.

This is the natural Python-MT pattern Raj et al. critique: one Python process,
N threads, each thread runs a query through claude-agent-sdk + RDKit. The GIL
is held during pure-Python orchestration glue (agent loop, span book-keeping)
even though RDKit's C++ releases it.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmark.configs import config_chemcrow_py
from benchmark.obs import Observer
from benchmark.sweep.summary import BatchResult


def _run_one(query: dict, out_dir: Path, config_name: str) -> tuple[str, bool]:
    """Run a single query through config_chemcrow_py.run() in this thread.

    Returns (query_id, ok). Failures are caught here so the thread pool
    doesn't propagate the exception and tear down sibling tasks.
    """
    qid = query["query_id"]
    obs = Observer(
        config=config_name,
        query_id=qid,
        out_dir=out_dir,
        forward_to=None,
        label=query.get("label"),
    )
    try:
        config_chemcrow_py.run(query, obs)
        return qid, True
    except Exception as e:
        # Observer.flush has already happened on root-span exit (or via the
        # finally inside config_chemcrow_py.run). Print so we see it in stderr.
        print(f"[py-mt] {qid} FAIL: {type(e).__name__}: {e}")
        return qid, False


def run_batch(queries: list[dict], out_dir: Path, concurrency: int, *, config_name: str = "chemcrow_py_mt") -> BatchResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    failures = 0
    trace_paths: list[Path] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for qid, ok in ex.map(lambda q: _run_one(q, out_dir, config_name), queries):
            tp = out_dir / f"{qid}.json"
            if tp.exists():
                trace_paths.append(tp)
            if not ok:
                failures += 1
    wall_ms = (time.monotonic() - t0) * 1000.0
    return BatchResult(wallclock_ms=wall_ms, trace_paths=trace_paths, num_failures=failures)
