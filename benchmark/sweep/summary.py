"""Sweep aggregation: shapes the per-(mode, N) summary.json.

Two concrete inputs feed this:
  - BatchResult: the wallclock + failure count from one harness invocation
                 (one of `--runs` repetitions). The harness writes one trace
                 JSON per query; this module reads them back.
  - Multiple BatchResults from `--runs` repetitions, plus a cold_start_ms,
    plus per-query metric lists, are folded into the summary.json shape:

  {
    "mode": "py-mt", "N": 16,
    "llm_transport": "claude-cli" | "anthropic-rest",
    "cold_start_ms": 2340.5,
    "warmup_queries": 4,
    "median_per_query_wallclock_ms_at_n1": 12450.0,  # only set for N=1; else null
    "runs": [
       {"wallclock_ms": 12450.0, "throughput_qps": 1.61, "num_failures": 0,
        "orchestration_ms": [...], "active_latency_ms": [...],
        "gross_wallclock_ms": [...]}
    ],
    "orchestration_ms":   {"p50": 423.1, "p90": 1102.4, "mean": 587.2},
    "active_latency_ms":  {"p50": ...,    "p90": ...,    "mean": ...},
    "gross_wallclock_ms": {"p50": ...,    "p90": ...,    "mean": ...}
  }

Percentiles in the top-level blocks are pooled across runs × queries.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.analysis.metrics import load_trace, orchestration_ms


@dataclass
class BatchResult:
    """One run of `--runs`. Wallclock and a list of per-query trace paths."""

    wallclock_ms: float
    trace_paths: list[Path]
    num_failures: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def per_query_metrics(self) -> dict[str, list[float]]:
        """Read the trace JSONs and produce the three per-query metric lists."""
        orch: list[float] = []
        active: list[float] = []
        gross: list[float] = []
        for p in self.trace_paths:
            try:
                tf = load_trace(p)
            except Exception:
                continue
            spans = tf.spans
            root = next((s for s in spans if s.parent_id is None), None)
            if root is None:
                continue
            gross.append(root.wall_time_ms)
            # Reuse the orchestration_ms helper for both fields below.
            orch.append(orchestration_ms(spans))
            # active_latency_ms: total - rate_limit_pause - retry_wait
            by_name: dict[str, float] = {}
            for s in spans:
                if s.parent_id is None:
                    continue
                by_name[s.name] = by_name.get(s.name, 0.0) + s.wall_time_ms
            retry_wait_ms = by_name.get("llm.retry_wait", 0.0)
            child_sum = sum(s.wall_time_ms for s in spans if s.parent_id == root.span_id)
            gap_ms = max(root.wall_time_ms - child_sum, 0.0)
            active.append(max(root.wall_time_ms - gap_ms - retry_wait_ms, 0.0))
        return {
            "orchestration_ms": orch,
            "active_latency_ms": active,
            "gross_wallclock_ms": gross,
        }


def _stat_block(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "mean": 0.0, "n": 0}
    return {
        "p50": float(statistics.median(values)),
        "p90": float(_quantile(values, 0.9)),
        "mean": float(statistics.fmean(values)),
        "n": len(values),
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    if len(sv) == 1:
        return sv[0]
    pos = q * (len(sv) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sv) - 1)
    frac = pos - lo
    return sv[lo] * (1 - frac) + sv[hi] * frac


def build_summary(
    *,
    mode: str,
    concurrency: int,
    cold_start_ms: float,
    warmup_queries: int,
    runs: list[BatchResult],
    llm_transport: str,
    cli_args: dict[str, Any],
) -> dict[str, Any]:
    """Fold per-run BatchResult records into the shape documented above."""
    runs_blob = []
    pooled_orch: list[float] = []
    pooled_active: list[float] = []
    pooled_gross: list[float] = []
    median_per_query_at_n1: float | None = None
    for r in runs:
        per = r.per_query_metrics()
        n = max(1, len(per["gross_wallclock_ms"]))
        runs_blob.append(
            {
                "wallclock_ms": r.wallclock_ms,
                "throughput_qps": (n / (r.wallclock_ms / 1000.0)) if r.wallclock_ms > 0 else 0.0,
                "num_failures": r.num_failures,
                "orchestration_ms": per["orchestration_ms"],
                "active_latency_ms": per["active_latency_ms"],
                "gross_wallclock_ms": per["gross_wallclock_ms"],
                **r.extra,
            }
        )
        pooled_orch.extend(per["orchestration_ms"])
        pooled_active.extend(per["active_latency_ms"])
        pooled_gross.extend(per["gross_wallclock_ms"])
    if concurrency == 1 and pooled_gross:
        median_per_query_at_n1 = float(statistics.median(pooled_gross))

    return {
        "mode": mode,
        "N": concurrency,
        "llm_transport": llm_transport,
        "cold_start_ms": cold_start_ms,
        "warmup_queries": warmup_queries,
        "median_per_query_wallclock_ms_at_n1": median_per_query_at_n1,
        "runs": runs_blob,
        "orchestration_ms": _stat_block(pooled_orch),
        "active_latency_ms": _stat_block(pooled_active),
        "gross_wallclock_ms": _stat_block(pooled_gross),
        "cli_args": cli_args,
    }


def write_point(
    out_dir: Path,
    summary: dict[str, Any],
    cold_start_trace: Path | None,
    cli_args: dict[str, Any],
) -> None:
    """Write summary.json + cli_args.json (+ optional cold_start.json) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out_dir / "cli_args.json").write_text(json.dumps(cli_args, indent=2, default=str))
    if cold_start_trace is not None and cold_start_trace.exists():
        # Copy (not move) so the original sits in traces/ for span analyses.
        (out_dir / "cold_start.json").write_text(cold_start_trace.read_text())


def update_index(sweep_root: Path, mode: str, concurrency: int, point_dir: Path) -> None:
    """Maintain sweep_index.json at the root so plots.py can discover all points."""
    idx_path = sweep_root / "sweep_index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text())
        except json.JSONDecodeError:
            idx = {"points": []}
    else:
        idx = {"points": []}
    points = [p for p in idx.get("points", []) if not (p.get("mode") == mode and p.get("N") == concurrency)]
    points.append(
        {
            "mode": mode,
            "N": concurrency,
            "dir": str(point_dir.relative_to(sweep_root)) if point_dir.is_relative_to(sweep_root) else str(point_dir),
            "summary_path": str((point_dir / "summary.json").resolve()),
        }
    )
    idx["points"] = sorted(points, key=lambda p: (p["mode"], p["N"]))
    idx_path.write_text(json.dumps(idx, indent=2))
