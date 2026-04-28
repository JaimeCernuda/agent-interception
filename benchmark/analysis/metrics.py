"""Read JSON traces, build a per-query metrics DataFrame.

Columns produced:
  config, query_id, total_latency_ms,
  tool_search_ms, tool_fetch_ms, tool_summarize_ms,
  tool_time_ms, llm_time_ms, framework_overhead_ms,
  tool_time_fraction, llm_time_fraction,
  num_tool_calls, num_retries, num_parse_errors,
  num_llm_turns, input_tokens_total, output_tokens_total
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from benchmark.span_schema import (
    LLM_GENERATE,
    ROOT_SPAN,
    TOOL_FETCH,
    TOOL_SEARCH,
    TOOL_SUMMARIZE,
    TraceFile,
)


def load_trace(path: Path) -> TraceFile:
    return TraceFile.from_dict(json.loads(path.read_text()))


def is_failed_trace(tf: TraceFile) -> bool:
    """A trace is 'failed' if any of its spans has status != 'ok' AND the run
    never produced a successful llm.generate. This filters out records of
    transport-level failures (e.g. HTTP 400 'credit balance too low' from the
    Go run that exhausted credits) without dropping legitimate measurements
    that happened to encounter a recoverable error mid-run.
    """
    has_failed_span = any(s.status != "ok" for s in tf.spans)
    has_successful_llm = any(
        s.kind == "llm"
        and s.status == "ok"
        and s.name == "llm.generate"
        and not bool(s.attrs.get("llm.rate_limited", False))
        for s in tf.spans
    )
    return has_failed_span and not has_successful_llm


def per_query_row(tf: TraceFile) -> dict:
    root = tf.root()
    by_name: dict[str, list[float]] = {}
    for s in tf.spans:
        if s.parent_id is None:
            continue
        by_name.setdefault(s.name, []).append(s.wall_time_ms)

    tool_search_ms = sum(by_name.get(TOOL_SEARCH, []))
    tool_fetch_ms = sum(by_name.get(TOOL_FETCH, []))
    tool_summarize_ms = sum(by_name.get(TOOL_SUMMARIZE, []))
    tool_time_ms = tool_search_ms + tool_fetch_ms + tool_summarize_ms
    llm_time_ms = sum(by_name.get(LLM_GENERATE, []))
    # llm.retry_wait spans measure pure backoff sleep time after a 429 - excluded
    # from llm_time_ms AND from the "active" budget so rate-limit transport cost
    # doesn't distort the cross-language comparison.
    retry_wait_ms = sum(by_name.get("llm.retry_wait", []))
    total_ms = root.wall_time_ms

    # Gap = time the root span was open but no tool/llm child span was.
    # Robust to parent_id mis-attribution (see orchestration_ms): we sum every
    # tool.* / llm.generate span regardless of its declared parent — they all
    # belong to this trace by construction. llm.retry_wait is excluded because
    # it's accounted for separately as wait time.
    child_sum = sum(
        s.wall_time_ms
        for s in tf.spans
        if s is not root and s.kind in ("tool", "llm") and s.name != "llm.retry_wait"
    )
    gap_ms = max(total_ms - child_sum, 0.0)
    # Overhead = time not accounted for by tools, LLM, retries, or gaps.
    overhead_ms = max(
        total_ms - tool_time_ms - llm_time_ms - retry_wait_ms - gap_ms, 0.0
    )

    num_tool_calls = sum(1 for s in tf.spans if s.kind == "tool")
    num_retries = sum(int(s.attrs.get("tool.retry_count", 0)) for s in tf.spans if s.kind == "tool")
    num_parse_errors = sum(
        1 for s in tf.spans if s.kind == "llm" and bool(s.attrs.get("llm.parse_error", False))
    )
    # num_llm_turns counts successful HTTP calls only. With the per-attempt
    # span refactor an LLM turn with one 429 retry would otherwise be counted
    # twice - once for the 429 attempt, once for the succeeding retry. Filter
    # to spans WITHOUT an llm.rate_limited=True flag.
    num_llm_turns = sum(
        1
        for s in tf.spans
        if s.kind == "llm" and s.name == "llm.generate" and not bool(s.attrs.get("llm.rate_limited", False))
    )
    num_retry_waits = sum(1 for s in tf.spans if s.name == "llm.retry_wait")
    input_tokens = sum(int(s.attrs.get("llm.input_tokens", 0)) for s in tf.spans if s.kind == "llm")
    output_tokens = sum(
        int(s.attrs.get("llm.output_tokens", 0)) for s in tf.spans if s.kind == "llm"
    )

    # "active" budget excludes both explicit inter-turn pauses AND 429 retry waits.
    # This is the "real work" time: tool + llm + framework overhead.
    active_ms = max(total_ms - gap_ms - retry_wait_ms, 0.0)
    # orchestration_ms isolates the GIL-relevant slice of active time:
    # tool execution + agent-loop glue, excluding LLM round-trips. Used by the
    # concurrency sweep (Phase 2) to expose Python-MT GIL contention.
    successful_llm_ms = sum(
        s.wall_time_ms
        for s in tf.spans
        if s.kind == "llm"
        and s.name == "llm.generate"
        and not bool(s.attrs.get("llm.rate_limited", False))
    )
    orchestration_ms = max(active_ms - successful_llm_ms, 0.0)
    return {
        "config": tf.config,
        "query_id": tf.query_id,
        "total_latency_ms": total_ms,
        "active_latency_ms": active_ms,
        "orchestration_ms": orchestration_ms,
        "tool_search_ms": tool_search_ms,
        "tool_fetch_ms": tool_fetch_ms,
        "tool_summarize_ms": tool_summarize_ms,
        "tool_time_ms": tool_time_ms,
        "llm_time_ms": llm_time_ms,
        "framework_overhead_ms": overhead_ms,
        "rate_limit_pause_ms": gap_ms,
        "retry_wait_ms": retry_wait_ms,
        "tool_time_fraction": (tool_time_ms / active_ms) if active_ms else 0.0,
        "llm_time_fraction": (llm_time_ms / active_ms) if active_ms else 0.0,
        "num_tool_calls": num_tool_calls,
        "num_retries": num_retries,
        "num_parse_errors": num_parse_errors,
        "num_llm_turns": num_llm_turns,
        "num_retry_waits": num_retry_waits,
        "input_tokens_total": input_tokens,
        "output_tokens_total": output_tokens,
    }


def orchestration_ms(spans: list) -> float:
    """Active latency minus successful llm.generate wall time.

    Mirrors the column produced by per_query_row(). Exposed as a top-level
    function so the sweep harness can compute it directly from a list of spans
    (avoiding a pandas dependency in the runner).

    Robust to parent_id mis-attribution: child_sum is computed from spans of
    kind in {"tool", "llm"} regardless of what their parent_id points at, so
    a synthetic span that happened to be parented under an in-flight tool
    span (instead of the root) still contributes correctly. The root and any
    llm.retry_wait spans are excluded.
    """
    if not spans:
        return 0.0
    root = next((s for s in spans if s.parent_id is None), None)
    total_ms = root.wall_time_ms if root else 0.0
    retry_wait_ms = sum(s.wall_time_ms for s in spans if s.name == "llm.retry_wait")
    child_sum = sum(
        s.wall_time_ms
        for s in spans
        if s is not root and s.kind in ("tool", "llm") and s.name != "llm.retry_wait"
    )
    gap_ms = max(total_ms - child_sum, 0.0)
    active = max(total_ms - gap_ms - retry_wait_ms, 0.0)
    llm_ms = sum(
        s.wall_time_ms
        for s in spans
        if s.kind == "llm"
        and s.name == "llm.generate"
        and not bool(s.attrs.get("llm.rate_limited", False))
    )
    return max(active - llm_ms, 0.0)


def build_df(trace_dirs: dict[str, Path]) -> pd.DataFrame:
    """trace_dirs: {'py': Path('benchmark/traces/py'), 'go': Path('benchmark/traces/go'), ...}

    Returns one row per trace. Config labels are whatever keys the caller passes.
    """
    rows: list[dict] = []
    for config, d in trace_dirs.items():
        if not d.exists():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                tf = load_trace(path)
            except Exception as e:
                print(f"WARN: could not load {path}: {e}")
                continue
            if is_failed_trace(tf):
                print(f"WARN: skipping failed trace {path}")
                continue
            row = per_query_row(tf)
            row["config"] = config  # authoritative
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["config", "query_id"]).reset_index(drop=True)


def per_config_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-config mean/P50/P95 of the cost-structure columns."""
    numeric_cols = [
        "total_latency_ms",
        "active_latency_ms",
        "orchestration_ms",
        "tool_search_ms",
        "tool_fetch_ms",
        "tool_summarize_ms",
        "tool_time_ms",
        "llm_time_ms",
        "framework_overhead_ms",
        "tool_time_fraction",
        "llm_time_fraction",
        "num_tool_calls",
        "num_retries",
        "num_parse_errors",
        "num_llm_turns",
    ]
    agg = df.groupby("config")[numeric_cols].agg(
        ["mean", lambda s: s.quantile(0.5), lambda s: s.quantile(0.95)]
    )
    agg.columns = [f"{c}_{stat}" for c, stat in agg.columns]
    # rename the lambdas
    agg.columns = [c.replace("<lambda_0>", "p50").replace("<lambda_1>", "p95") for c in agg.columns]
    return agg.reset_index()


def cli() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--traces-root", type=Path, default=Path("benchmark/traces"))
    ap.add_argument("--out", type=Path, default=Path("benchmark/results"))
    ap.add_argument("--configs", nargs="+", default=["py", "go"], help="subdirs under --traces-root")
    args = ap.parse_args()
    dirs = {c: args.traces_root / c for c in args.configs}
    df = build_df(dirs)
    args.out.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out / "metrics.csv", index=False)
    summary = per_config_summary(df)
    summary.to_csv(args.out / "metrics_per_config.csv", index=False)
    print(f"Wrote {args.out / 'metrics.csv'} ({len(df)} rows)")
    print(f"Wrote {args.out / 'metrics_per_config.csv'}")
    print(summary.to_string())


if __name__ == "__main__":
    cli()
