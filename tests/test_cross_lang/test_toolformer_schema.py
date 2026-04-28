"""Cross-language schema test for the Toolformer benchmark workload.

Mirrors test_sweagent_schema.py but for the Toolformer span tree:
agent.query → 2× llm.generate + tool.calculator. Both emitters run with a
fixed clock so wall_time_ms and start_ns/end_ns are byte-equal across
languages, and we can diff by name + position.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY_GOLDEN = REPO / "benchmark-go" / "testdata" / "toolformer_golden_trace.json"
GO_MODULE = REPO / "benchmark-go"

ID_FIELDS = {"trace_id", "span_id", "parent_id"}
EXACT_MATCH_FIELDS = {
    "name",
    "start_ns",
    "end_ns",
    "wall_time_ms",
    "kind",
    "status",
    "error",
    "attrs",
}

EXPECTED_SPAN_NAMES = {
    "agent.query",
    "llm.generate",
    "tool.calculator",
}


def _regenerate_python_golden() -> None:
    subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "benchmark",
            "python",
            "benchmark-go/testdata/generate_toolformer_golden.py",
        ],
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), **_minimal_env()},
        check=True,
        capture_output=True,
    )


def _run_go_emitter(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["go", "run", "./cmd/toolformer_golden", "-out", str(out_dir)],
        cwd=GO_MODULE,
        check=True,
        capture_output=True,
    )


def _minimal_env() -> dict[str, str]:
    import os

    keep = ("HOME", "PATH", "USER", "LANG", "LC_ALL", "SHELL", "TMPDIR")
    return {k: v for k, v in os.environ.items() if k in keep}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _spans_by_signature(trace: dict) -> dict[tuple[str, int], dict]:
    """Index spans by (name, position-among-same-name). Multiple llm.generate
    spans share a name, so position keeps them addressable."""
    counts: dict[str, int] = {}
    out: dict[tuple[str, int], dict] = {}
    for s in trace["spans"]:
        i = counts.get(s["name"], 0)
        out[(s["name"], i)] = s
        counts[s["name"]] = i + 1
    return out


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    if not shutil.which("go"):
        pytest.skip("go toolchain not installed")
    _regenerate_python_golden()
    go_out = tmp_path_factory.mktemp("go_toolformer_out")
    _run_go_emitter(go_out)
    py = _load(PY_GOLDEN)
    go = _load(go_out / "fixture_toolformer_001.json")
    return py, go


def test_top_level_keys_match(traces):
    py, go = traces
    assert set(py.keys()) == set(go.keys()), (
        f"top-level key mismatch: only-py={set(py) - set(go)} only-go={set(go) - set(py)}"
    )


def test_top_level_values_match(traces):
    py, go = traces
    for k in ("config", "query_id"):
        assert py[k] == go[k], f"{k} differs: py={py[k]!r} go={go[k]!r}"
    for _, t in (("py", py), ("go", go)):
        assert len(t["trace_id"]) == 32
        int(t["trace_id"], 16)


def test_span_count_matches_fixture(traces):
    py, go = traces
    # Fixture: 1 root + 2 llm.generate + 1 tool.calculator = 4 spans.
    assert len(py["spans"]) == 4
    assert len(go["spans"]) == 4


def test_expected_span_names_present(traces):
    py, go = traces
    py_names = {s["name"] for s in py["spans"]}
    go_names = {s["name"] for s in go["spans"]}
    assert EXPECTED_SPAN_NAMES.issubset(py_names), f"py missing names: {EXPECTED_SPAN_NAMES - py_names}"
    assert EXPECTED_SPAN_NAMES.issubset(go_names), f"go missing names: {EXPECTED_SPAN_NAMES - go_names}"
    assert py_names == go_names, f"name set differs: py-only={py_names - go_names} go-only={go_names - py_names}"


def test_tree_structure_matches(traces):
    py, go = traces
    for label, t in (("py", py), ("go", go)):
        roots = [s for s in t["spans"] if s["parent_id"] is None]
        assert len(roots) == 1, f"{label}: expected 1 root span"
        tid = roots[0]["trace_id"]
        for s in t["spans"]:
            assert s["trace_id"] == tid, f"{label}: trace_id not consistent"
            if s["parent_id"] is not None:
                assert any(t2["span_id"] == s["parent_id"] for t2 in t["spans"]), (
                    f"{label}: dangling parent_id on {s['name']}"
                )

    # Toolformer is single-step: every non-root span hangs directly off the
    # root. No nested sub-spans (calculator has no decomposition).
    for label, t in (("py", py), ("go", go)):
        root_id = next(s for s in t["spans"] if s["parent_id"] is None)["span_id"]
        for s in t["spans"]:
            if s["parent_id"] is None:
                continue
            assert s["parent_id"] == root_id, (
                f"{label}: {s['name']} parent={s['parent_id'][:8]} not root={root_id[:8]}"
            )


def test_per_span_keys_match(traces):
    py, go = traces
    py_by = _spans_by_signature(py)
    go_by = _spans_by_signature(go)
    assert set(py_by) == set(go_by), (
        f"span signature set mismatch: py-only={set(py_by) - set(go_by)} "
        f"go-only={set(go_by) - set(py_by)}"
    )
    for sig in py_by:
        pk = set(py_by[sig].keys())
        gk = set(go_by[sig].keys())
        assert pk == gk, f"{sig}: span field set differs: only-py={pk - gk} only-go={gk - pk}"


def test_per_span_attr_keys_match(traces):
    """Each span's attrs key SET must agree across languages, even if values
    legitimately differ. Catches a Go-only attr that Python forgot."""
    py, go = traces
    py_by = _spans_by_signature(py)
    go_by = _spans_by_signature(go)
    for sig in py_by:
        pa = set(py_by[sig]["attrs"].keys())
        ga = set(go_by[sig]["attrs"].keys())
        assert pa == ga, f"{sig}: attrs key set differs: only-py={pa - ga} only-go={ga - pa}"


def test_per_span_exact_fields_match(traces):
    py, go = traces
    py_by = _spans_by_signature(py)
    go_by = _spans_by_signature(go)
    for sig, psp in py_by.items():
        gsp = go_by[sig]
        for field in EXACT_MATCH_FIELDS:
            assert psp[field] == gsp[field], (
                f"{sig}.{field} differs: py={psp[field]!r} go={gsp[field]!r}"
            )


def test_id_field_types_match(traces):
    py, go = traces
    py_by = _spans_by_signature(py)
    go_by = _spans_by_signature(go)
    for sig in py_by:
        for field in ID_FIELDS:
            pv, gv = py_by[sig][field], go_by[sig][field]
            if pv is None:
                assert gv is None, f"{sig}.{field} type differs: py=None go={gv!r}"
            else:
                assert isinstance(gv, str), f"{sig}.{field} go value not a string"
                assert len(pv) == len(gv), f"{sig}.{field} length differs: py={len(pv)} go={len(gv)}"


def test_root_has_agent_cpu_time_ms(traces):
    """Phase-1 invariant: the root span MUST carry agent.cpu_time_ms."""
    py, go = traces
    for label, t in (("py", py), ("go", go)):
        root = next(s for s in t["spans"] if s["parent_id"] is None)
        assert "agent.cpu_time_ms" in root["attrs"], (
            f"{label}: agent.query root missing agent.cpu_time_ms"
        )


def test_calculator_carries_expected_attrs(traces):
    """The tool.calculator span tracks the expression and the input hash,
    plus either a result (success) or error (failure). The fixture is a
    success case; both must be present."""
    py, go = traces
    for label, t in (("py", py), ("go", go)):
        calc = next(s for s in t["spans"] if s["name"] == "tool.calculator")
        for required in ("expression", "tool.name", "tool.input_hash", "result"):
            assert required in calc["attrs"], (
                f"{label}: tool.calculator missing required attr {required!r}"
            )
        assert calc["attrs"]["tool.name"] == "calculator"
