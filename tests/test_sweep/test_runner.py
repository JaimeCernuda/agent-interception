"""Sweep runner tests.

Mocks the harness functions so we don't actually shell out to RDKit / Go / Claude
CLI. Verifies argparse, warmup subset selection, py-mp guard, and that py-mp
mode does NOT use a long-lived process pool (it uses subprocess.Popen).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from benchmark.sweep import modes as modes_pkg
from benchmark.sweep import runner
from benchmark.sweep.summary import BatchResult


def _fake_trace(qid: str, dir: Path) -> Path:
    p = dir / f"{qid}.json"
    p.write_text(
        json.dumps(
            {
                "trace_id": "0" * 32,
                "config": "fake",
                "query_id": qid,
                "label": "medium",
                "spans": [
                    {
                        "name": "agent.query",
                        "trace_id": "0" * 32,
                        "span_id": "0" * 16,
                        "parent_id": None,
                        "start_ns": 0,
                        "end_ns": 1_000_000,
                        "wall_time_ms": 1.0,
                        "cpu_time_ms": 0.0,
                        "kind": "root",
                        "attrs": {},
                        "status": "ok",
                        "error": None,
                    }
                ],
            }
        )
    )
    return p


@pytest.fixture
def fake_harness(monkeypatch):
    """Replace each mode's harness with a stub that just writes a fake trace per query."""

    def _stub(queries, out_dir, concurrency, **kwargs):  # noqa: ARG001
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [_fake_trace(q["query_id"], out_dir) for q in queries]
        return BatchResult(wallclock_ms=10.0, trace_paths=paths, num_failures=0)

    monkeypatch.setitem(modes_pkg.HARNESSES, "py-mt", _stub)
    monkeypatch.setitem(modes_pkg.HARNESSES, "py-mp", _stub)
    monkeypatch.setitem(modes_pkg.HARNESSES, "go", _stub)
    return _stub


@pytest.fixture
def queries_file(tmp_path):
    p = tmp_path / "queries.json"
    p.write_text(
        json.dumps(
            {
                "queries": [
                    {"query_id": f"q{i:03d}", "label": "medium" if i < 5 else "heavy",
                     "molecule_name": f"m{i}", "query_text": "dummy"}
                    for i in range(10)
                ]
            }
        )
    )
    return p


# -------------------- runner argparse & dispatch ----------------------------


def test_runner_single_point(tmp_path, queries_file, fake_harness):
    rc = runner.main([
        "--mode", "py-mt", "--concurrency", "2",
        "--runs", "1", "--warmup-queries", "0",
        "--queries", str(queries_file),
        "--out-root", str(tmp_path / "sweep"),
        "--skip-cold-start",
    ])
    assert rc == 0
    point = tmp_path / "sweep" / "py-mt_n2"
    assert (point / "summary.json").exists()
    assert (point / "cli_args.json").exists()
    summary = json.loads((point / "summary.json").read_text())
    assert summary["N"] == 2
    assert summary["mode"] == "py-mt"
    # No cold start because we passed --skip-cold-start.
    assert summary["cold_start_ms"] == 0.0


def test_runner_sweep_iterates_default_ns(tmp_path, queries_file, fake_harness):
    rc = runner.main([
        "--mode", "go", "--sweep", "--max-n", "8",
        "--runs", "1", "--warmup-queries", "0",
        "--queries", str(queries_file),
        "--out-root", str(tmp_path / "sweep"),
        "--skip-cold-start",
    ])
    assert rc == 0
    idx = json.loads((tmp_path / "sweep" / "sweep_index.json").read_text())
    ns = sorted(p["N"] for p in idx["points"])
    assert ns == [1, 2, 4, 8]


def test_warmup_subset_balances_medium_heavy():
    queries = [
        {"query_id": "q1", "label": "medium"},
        {"query_id": "q2", "label": "medium"},
        {"query_id": "q3", "label": "heavy"},
        {"query_id": "q4", "label": "heavy"},
    ]
    sub = runner._warmup_subset(queries, 4)
    assert {q["label"] for q in sub} == {"medium", "heavy"}
    assert len(sub) == 4
    assert runner._warmup_subset(queries, 0) == []


def test_pympp_cap_warns_above_2x_cpu(monkeypatch):
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 1)  # cap at 2
    n, msg = runner._cap_concurrency_for_pympp("py-mp", 4)
    assert n == 4
    assert msg is not None and "broken" in msg

    n2, msg2 = runner._cap_concurrency_for_pympp("py-mp", 1)
    assert n2 == 1
    assert msg2 is None

    # Other modes are not capped.
    n3, msg3 = runner._cap_concurrency_for_pympp("py-mt", 9999)
    assert n3 == 9999 and msg3 is None


def test_llm_transport_classification():
    # Phase-2.5: all three modes route through the Claude Code CLI now (Go
    # was moved off the metered API to remove the LLM-transport asymmetry).
    assert runner._llm_transport("py-mt") == "claude-cli"
    assert runner._llm_transport("py-mp") == "claude-cli"
    assert runner._llm_transport("go") == "claude-cli"


# -------------------- py-mp implementation guard ----------------------------


def test_py_mp_does_not_use_processpoolexecutor():
    """Spec correction: py-mp MUST use subprocess.Popen (or .run) per query,
    NOT ProcessPoolExecutor (even with max_tasks_per_child=1, the pool itself
    is long-lived and amortizes import cost across the queue).

    We walk the AST so docstring/comment matches don't false-positive.
    """
    import ast

    from benchmark.sweep.modes import py_mp

    tree = ast.parse(inspect.getsource(py_mp))
    imported_names: set[str] = set()
    referenced_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            imported_names.add(mod)
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)
    forbidden = {"ProcessPoolExecutor", "multiprocessing"}
    bad = forbidden & (imported_names | referenced_names)
    assert not bad, f"py-mp must not use {bad}; spec uses subprocess.Popen + Semaphore"
    # Positive shape — uses subprocess + a semaphore.
    assert "subprocess" in imported_names
    assert "Semaphore" in (imported_names | referenced_names) or "BoundedSemaphore" in referenced_names


def test_py_mt_uses_threadpool():
    """py-mt should use the natural Python-MT pattern: a long-lived thread pool."""
    from benchmark.sweep.modes import py_mt

    src = inspect.getsource(py_mt)
    assert "ThreadPoolExecutor" in src


def test_go_invokes_go_binary():
    from benchmark.sweep.modes import go

    src = inspect.getsource(go)
    assert "./cmd/chemcrow" in src
    assert "WALLCLOCK_MS" in src


# -------------------- BatchResult schema lock --------------------------------


def test_batch_result_minimal_construction():
    br = BatchResult(wallclock_ms=12.5, trace_paths=[], num_failures=0)
    assert br.wallclock_ms == 12.5
    assert br.trace_paths == []
    assert br.num_failures == 0
    assert br.extra == {}
