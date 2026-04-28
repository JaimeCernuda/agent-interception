"""Unit tests for the sweep aggregator.

Uses a fabricated trace JSON on disk so we don't depend on the real RDKit /
LLM stack; this is the cheap-and-fast layer of the sweep test suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.sweep.summary import (
    BatchResult,
    _quantile,
    _stat_block,
    build_summary,
    update_index,
    write_point,
)


def _fake_trace(query_id: str, *, total_ms: float, llm_ms: float, tool_ms: float) -> dict:
    """One-root + one-llm + one-tool trace, matching the Phase-1 schema."""
    start = 1_000_000_000_000  # arbitrary epoch ns
    return {
        "trace_id": "0" * 32,
        "config": "fake",
        "query_id": query_id,
        "label": "medium",
        "spans": [
            {
                "name": "agent.query",
                "trace_id": "0" * 32,
                "span_id": "0" * 16,
                "parent_id": None,
                "start_ns": start,
                "end_ns": start + int(total_ms * 1e6),
                "wall_time_ms": total_ms,
                "cpu_time_ms": 0.5,
                "kind": "root",
                "attrs": {"config": "fake", "query_id": query_id},
                "status": "ok",
                "error": None,
            },
            {
                "name": "llm.generate",
                "trace_id": "0" * 32,
                "span_id": "1" * 16,
                "parent_id": "0" * 16,
                "start_ns": start,
                "end_ns": start + int(llm_ms * 1e6),
                "wall_time_ms": llm_ms,
                "cpu_time_ms": 0.1,
                "kind": "llm",
                "attrs": {
                    "llm.model": "claude-haiku-4-5",
                    "llm.provider": "anthropic",
                    "llm.parse_error": False,
                },
                "status": "ok",
                "error": None,
            },
            {
                "name": "tool.smiles_to_3d",
                "trace_id": "0" * 32,
                "span_id": "2" * 16,
                "parent_id": "0" * 16,
                "start_ns": start + int(llm_ms * 1e6),
                "end_ns": start + int((llm_ms + tool_ms) * 1e6),
                "wall_time_ms": tool_ms,
                "cpu_time_ms": 0.1,
                "kind": "tool",
                "attrs": {"tool.name": "smiles_to_3d", "tool.smiles": "CC"},
                "status": "ok",
                "error": None,
            },
        ],
    }


def _write_traces(dir: Path, recipes: list[tuple[str, float, float, float]]) -> list[Path]:
    dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for qid, total_ms, llm_ms, tool_ms in recipes:
        p = dir / f"{qid}.json"
        p.write_text(json.dumps(_fake_trace(qid, total_ms=total_ms, llm_ms=llm_ms, tool_ms=tool_ms)))
        paths.append(p)
    return paths


def test_quantile_basic():
    assert _quantile([], 0.5) == 0.0
    assert _quantile([7.0], 0.5) == 7.0
    assert _quantile([1, 2, 3, 4, 5], 0.5) == pytest.approx(3.0)
    assert _quantile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)


def test_stat_block_handles_empty():
    b = _stat_block([])
    assert b == {"p50": 0.0, "p90": 0.0, "mean": 0.0, "n": 0}


def test_batch_result_per_query_metrics(tmp_path):
    dir = tmp_path / "traces"
    dir.mkdir()
    paths = _write_traces(
        dir,
        [
            # (qid, total, llm, tool) — orchestration = total - llm
            ("q001", 1100.0, 1000.0, 100.0),
            ("q002", 5400.0, 5000.0, 400.0),
        ],
    )
    br = BatchResult(wallclock_ms=6500.0, trace_paths=paths, num_failures=0)
    metrics = br.per_query_metrics()
    assert sorted(metrics.keys()) == ["active_latency_ms", "gross_wallclock_ms", "orchestration_ms"]
    assert metrics["gross_wallclock_ms"] == [1100.0, 5400.0]
    # active = total - rate_limit_pause - retry_wait. There's no retry/gap in
    # the fake fixture, so active == total.
    assert metrics["active_latency_ms"] == [1100.0, 5400.0]
    # orch = active - successful llm = total - llm.
    assert metrics["orchestration_ms"] == [100.0, 400.0]


def test_build_summary_pools_across_runs(tmp_path):
    run0_dir = tmp_path / "run0"; run0_dir.mkdir()
    run1_dir = tmp_path / "run1"; run1_dir.mkdir()
    paths0 = _write_traces(run0_dir, [("q001", 1000, 900, 100), ("q002", 2000, 1800, 200)])
    paths1 = _write_traces(run1_dir, [("q001", 1100, 1000, 100), ("q002", 2200, 2000, 200)])
    runs = [
        BatchResult(wallclock_ms=2000.0, trace_paths=paths0),
        BatchResult(wallclock_ms=2300.0, trace_paths=paths1),
    ]
    summary = build_summary(
        mode="py-mt",
        concurrency=2,
        cold_start_ms=1500.0,
        warmup_queries=4,
        runs=runs,
        llm_transport="claude-cli",
        cli_args={"x": 1},
    )
    assert summary["mode"] == "py-mt"
    assert summary["N"] == 2
    assert summary["cold_start_ms"] == 1500.0
    assert summary["llm_transport"] == "claude-cli"
    # Pooled across 2 runs × 2 queries = 4 values.
    assert summary["orchestration_ms"]["n"] == 4
    assert summary["gross_wallclock_ms"]["n"] == 4
    # median orch should be 100 (4×100s and 4×200s? — actually 2×100 + 2×200)
    assert summary["orchestration_ms"]["p50"] in (100.0, 150.0, 200.0)
    # throughput per run: 2 queries / (wallclock_s)
    assert summary["runs"][0]["throughput_qps"] == pytest.approx(2 / 2.0, rel=1e-6)
    # N!=1 so this should be null
    assert summary["median_per_query_wallclock_ms_at_n1"] is None


def test_build_summary_records_n1_median_when_n_is_one(tmp_path):
    run0 = tmp_path / "run0"; run0.mkdir()
    paths = _write_traces(run0, [("q001", 1000, 900, 100), ("q002", 2000, 1900, 100)])
    summary = build_summary(
        mode="py-mp", concurrency=1, cold_start_ms=10.0, warmup_queries=0,
        runs=[BatchResult(wallclock_ms=3000.0, trace_paths=paths)],
        llm_transport="claude-cli", cli_args={},
    )
    assert summary["median_per_query_wallclock_ms_at_n1"] == 1500.0


def test_write_point_and_update_index(tmp_path):
    point = tmp_path / "py-mt_n2"
    paths = _write_traces(tmp_path / "traces", [("q001", 1000, 900, 100)])
    runs = [BatchResult(wallclock_ms=1000.0, trace_paths=paths)]
    summary = build_summary(
        mode="py-mt", concurrency=2, cold_start_ms=10.0, warmup_queries=0,
        runs=runs, llm_transport="claude-cli", cli_args={"a": "b"},
    )
    write_point(point, summary, paths[0], cli_args={"a": "b"})
    assert (point / "summary.json").exists()
    assert (point / "cli_args.json").exists()
    assert (point / "cold_start.json").exists()
    update_index(tmp_path, "py-mt", 2, point)
    idx = json.loads((tmp_path / "sweep_index.json").read_text())
    assert len(idx["points"]) == 1
    assert idx["points"][0]["mode"] == "py-mt"
    assert idx["points"][0]["N"] == 2

    # Updating again should NOT duplicate the point.
    update_index(tmp_path, "py-mt", 2, point)
    idx2 = json.loads((tmp_path / "sweep_index.json").read_text())
    assert len(idx2["points"]) == 1
