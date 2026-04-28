"""Tiny batch driver for the Toolformer Python config.

Used by the Phase 1 acceptance / diagnostic: runs N queries with bounded
concurrency, then reports per-query correctness against expected_answer.

Usage:
  uv run --group benchmark python -m benchmark.run_toolformer_batch \\
    --queries benchmark/queries/toolformer_20.json \\
    --query-ids q01,q06,q11 \\
    --concurrency 8 \\
    --out benchmark/output/toolformer_diag_n8

Phase-2 sweep harnesses will replace this; for now it's the smallest thing
that lets us measure N=1 vs N=8 wall/CPU on a stable subset.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmark.configs.config_toolformer_py import run as run_py
from benchmark.obs import Observer


def _extract_numeric(text: str) -> float | None:
    """Pull the most likely numeric answer out of the agent's final text.

    Strategy: prefer numbers in the final sentence/paragraph (agents end with
    "the answer is X"). Fall back to the last number in the whole text.
    """
    if not text:
        return None
    # Last paragraph: agents often write a final summary line.
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    candidates: list[str] = []
    if paras:
        candidates = re.findall(r"-?\d+(?:\.\d+)?", paras[-1])
    if not candidates:
        candidates = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not candidates:
        return None
    try:
        return float(candidates[-1])
    except ValueError:
        return None


def _trace_calc_results(trace_path: Path) -> list[float]:
    """Read a trace JSON and return all calculator results in span order."""
    if not trace_path.exists():
        return []
    blob = json.loads(trace_path.read_text())
    spans = sorted(blob["spans"], key=lambda s: s["start_ns"])
    return [
        float(s["attrs"]["result"])
        for s in spans
        if s["name"] == "tool.calculator" and "result" in s["attrs"]
    ]


def _approx_eq(a: float, b: float, tol: float = 0.02) -> bool:
    if b == 0:
        return abs(a) < 0.01
    rel = abs(a - b) / max(abs(b), 1.0)
    return rel <= tol or abs(a - b) < 0.01


def _is_correct(answer_text: str, expected: float, trace_path: Path | None = None) -> bool:
    """A query is "correct" if EITHER:
      - the last calculator result matches expected (the agent's last computation), OR
      - the number extracted from the final paragraph of the answer matches.
    Tolerant relative match (2% rel, 0.01 abs floor)."""
    if trace_path is not None:
        calc = _trace_calc_results(trace_path)
        if calc and _approx_eq(calc[-1], expected):
            return True
    n = _extract_numeric(answer_text)
    if n is not None and _approx_eq(n, expected):
        return True
    return False


def _run_one(q: dict, out_dir: Path, config_name: str) -> tuple[str, str, float | None, bool, float]:
    obs = Observer(
        config=config_name,
        query_id=q["query_id"],
        out_dir=out_dir,
        label=q.get("category"),
    )
    t0 = time.time()
    try:
        answer = run_py(q, obs)
    except Exception as e:
        return q["query_id"], f"ERROR: {e!r}", None, False, time.time() - t0
    expected = float(q.get("expected_answer", 0.0))
    trace_path = out_dir / f"{q['query_id']}.json"
    calc_results = _trace_calc_results(trace_path)
    extracted = calc_results[-1] if calc_results else _extract_numeric(answer)
    correct = _is_correct(answer, expected, trace_path)
    return q["query_id"], answer, extracted, correct, time.time() - t0


async def _async_main(queries: list[dict], out_dir: Path, config_name: str, concurrency: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple] = []

    async def _wrap(q: dict):
        async with sem:
            return await asyncio.to_thread(_run_one, q, out_dir, config_name)

    print(f"[batch] {len(queries)} queries  concurrency={concurrency}  out={out_dir}")
    wall_start = time.time()
    futs = [_wrap(q) for q in queries]
    for fut in asyncio.as_completed(futs):
        qid, answer, extracted, correct, dt = await fut
        results.append((qid, answer, extracted, correct, dt))
        flag = "OK " if correct else "FAIL"
        print(f"  [{flag}] {qid}  extracted={extracted}  ({dt:.2f}s)")
    wall_total = time.time() - wall_start

    # Also print expected vs actual for failures
    print(f"\n[batch] wallclock={wall_total:.2f}s")
    n_correct = sum(1 for _, _, _, c, _ in results if c)
    print(f"[batch] correctness: {n_correct}/{len(results)}")
    if n_correct < len(results):
        print("[batch] failures:")
        for qid, _ans, ext, c, _dt in results:  # noqa: F841
            if not c:
                exp = next(q["expected_answer"] for q in queries if q["query_id"] == qid)
                print(f"   {qid}: expected={exp} extracted={ext}")
    return 0 if n_correct == len(results) else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path,
                        default=Path("benchmark/queries/toolformer_20.json"))
    parser.add_argument("--out", type=Path,
                        default=Path("benchmark/output/toolformer_batch"))
    parser.add_argument("--config", default="toolformer_py")
    parser.add_argument("--query-ids", default="",
                        help="comma-separated subset of query_ids; empty = all")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    load_dotenv(Path("benchmark/.env"))
    blob = json.loads(args.queries.read_text())
    queries = blob["queries"]
    if args.query_ids:
        wanted = set(s.strip() for s in args.query_ids.split(",") if s.strip())
        queries = [q for q in queries if q["query_id"] in wanted]
        if not queries:
            print(f"no matching query_ids in {wanted}", file=sys.stderr)
            return 2

    return asyncio.run(_async_main(queries, args.out, args.config, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
