# pyright: reportMissingImports=false
"""Analyzer for the Toolformer Phase-1 diagnostic.

Reads traces produced by the four diagnostic runs and computes:
  - the 6-row table (lang × N × {q01, q06, q11} medians)
  - correctness across all 20 Python queries
  - span tree dump for q01 in both languages

Pure read-only — no API calls.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUERIES = json.loads((REPO / "benchmark/queries/toolformer_20.json").read_text())["queries"]
QMAP = {q["query_id"]: q for q in QUERIES}
DIAG_QIDS = ["q01", "q06", "q11"]


def _load_trace(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _root_span(trace: dict) -> dict:
    return next(s for s in trace["spans"] if s["parent_id"] is None)


def _calc_results(trace: dict) -> list[float]:
    spans = sorted(trace["spans"], key=lambda s: s["start_ns"])
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


def _correctness_from_dir(d: Path) -> tuple[int, list[tuple[str, float, float | None]]]:
    """Returns (n_correct, list_of_(qid, expected, last_calc_or_None) for failures)."""
    correct = 0
    failures: list[tuple[str, float, float | None]] = []
    for q in QUERIES:
        trace = _load_trace(d / f"{q['query_id']}.json")
        if trace is None:
            failures.append((q["query_id"], q["expected_answer"], None))
            continue
        calcs = _calc_results(trace)
        last = calcs[-1] if calcs else None
        if last is not None and _approx_eq(last, float(q["expected_answer"])):
            correct += 1
        else:
            failures.append((q["query_id"], float(q["expected_answer"]), last))
    return correct, failures


def _diag_row(d: Path, qids: list[str]) -> dict:
    """Compute median metrics across the given qids in dir d."""
    cpu = []
    wall = []
    tool_walls = []
    llm_walls = []
    n_calls = []
    for qid in qids:
        trace = _load_trace(d / f"{qid}.json")
        if trace is None:
            continue
        root = _root_span(trace)
        cpu.append(root["attrs"].get("agent.cpu_time_ms", 0.0))
        wall.append(root["wall_time_ms"])
        for s in trace["spans"]:
            if s["name"] == "tool.calculator":
                tool_walls.append(s["wall_time_ms"])
            elif s["name"] == "llm.generate":
                llm_walls.append(s["wall_time_ms"])
        n_calls.append(sum(1 for s in trace["spans"] if s["name"] == "tool.calculator"))

    def _med(xs):
        return statistics.median(xs) if xs else 0.0

    return {
        "median_cpu_ms": _med(cpu),
        "median_tool_calc_ms": _med(tool_walls),
        "median_llm_gen_ms": _med(llm_walls),
        "median_wall_ms": _med(wall),
        "median_n_calls": _med(n_calls),
        "n_traces": sum(1 for qid in qids if (d / f"{qid}.json").exists()),
    }


def _print_span_tree(trace: dict) -> None:
    spans = sorted(trace["spans"], key=lambda s: s["start_ns"])
    root = _root_span(trace)
    print(f"  trace_id={trace['trace_id']}  config={trace['config']}  query_id={trace['query_id']}")
    print(f"  agent.cpu_time_ms = {root['attrs'].get('agent.cpu_time_ms', '?'):.3f} ms")
    print(f"  agent.num_tool_calls = {root['attrs'].get('agent.num_tool_calls', '?')}")
    for s in spans:
        depth = "" if s["parent_id"] is None else "  "
        flag = "ROOT" if s["parent_id"] is None else (s["parent_id"][:8])
        attrs_short = {
            k: (v[:40] + "..." if isinstance(v, str) and len(v) > 40 else v)
            for k, v in s["attrs"].items()
            if k in ("expression", "result", "llm.turn", "llm.has_tool_use", "llm.stop_reason")
        }
        print(
            f"    {depth}{s['name']:25s} parent={flag:8s} "
            f"wall={s['wall_time_ms']:8.2f}ms cpu={s['cpu_time_ms']:8.4f}ms  {attrs_short}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--py-n1", type=Path, default=Path("benchmark/output/toolformer_diag_n1"))
    parser.add_argument("--py-n8", type=Path, default=Path("benchmark/output/toolformer_diag_n8"))
    parser.add_argument("--go-n1", type=Path, default=Path("benchmark/output/toolformer_go_diag_n1"))
    parser.add_argument("--go-n8", type=Path, default=Path("benchmark/output/toolformer_go_diag_n8"))
    args = parser.parse_args()

    runs = [
        ("python", 1, args.py_n1),
        ("python", 8, args.py_n8),
        ("go    ", 1, args.go_n1),
        ("go    ", 8, args.go_n8),
    ]

    print("=" * 110)
    print("DIAGNOSTIC TABLE (medians across q01, q06, q11)")
    print("=" * 110)
    print(f"{'lang':6s}  {'N':>3s}  {'cpu_ms':>10s}  {'calc_ms':>10s}  "
          f"{'llm_ms':>10s}  {'wall_ms':>10s}  {'#calls':>6s}  {'traces':>7s}")
    print("-" * 110)
    for lang, n, d in runs:
        r = _diag_row(d, DIAG_QIDS)
        print(f"{lang}  {n:>3d}  {r['median_cpu_ms']:>10.2f}  "
              f"{r['median_tool_calc_ms']:>10.4f}  "
              f"{r['median_llm_gen_ms']:>10.2f}  "
              f"{r['median_wall_ms']:>10.2f}  "
              f"{int(r['median_n_calls']):>6d}  "
              f"{r['n_traces']:>7d}")
    print("=" * 110)

    print()
    print("=" * 110)
    print("CORRECTNESS (Python, all 20 queries)")
    print("=" * 110)
    py_n8_correct, py_n8_fail = _correctness_from_dir(args.py_n8)
    print(f"  {py_n8_correct}/20 correct (last-calc heuristic, 2% rel tolerance)")
    if py_n8_fail:
        print("  failures:")
        for qid, exp, got in py_n8_fail:
            print(f"    {qid}: expected={exp!r:<10}  got={got!r:<10}  "
                  f"category={QMAP[qid]['category']}")

    print()
    print("=" * 110)
    print("Q01 SPAN TREE — Python (toolformer_diag_n1)")
    print("=" * 110)
    py_q01 = _load_trace(args.py_n1 / "q01.json")
    if py_q01:
        _print_span_tree(py_q01)
    else:
        print("  not found")

    print()
    print("=" * 110)
    print("Q01 SPAN TREE — Go (toolformer_go_diag_n1)")
    print("=" * 110)
    go_q01 = _load_trace(args.go_n1 / "q01.json")
    if go_q01:
        _print_span_tree(go_q01)
    else:
        print("  not found")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
