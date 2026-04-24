"""Cross-language schema-equivalence test.

Builds the Go golden emitter, runs it with a fixed clock sequence, runs the
Python golden emitter with the MATCHING sequence, then diffs the two JSON
trace files. Any drift in field names, types, tree shape, or timing
arithmetic fails this test.

This is the load-bearing correctness test for the Py-vs-Go benchmark:
if analysis/ reads both outputs through the same code path, they must have
byte-compatible shape.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY_GOLDEN = REPO / "benchmark-go" / "testdata" / "golden_trace.json"
GO_MODULE = REPO / "benchmark-go"

# Fields whose VALUES are random per-run (trace/span ids). Structural checks
# only on these - see _assert_tree_shape_equal.
ID_FIELDS = {"trace_id", "span_id", "parent_id"}

# Fields whose values must match BYTE-FOR-BYTE across Py and Go given the
# fixed clock + fixed input attrs.
EXACT_MATCH_FIELDS = {
    "name",
    "start_ns",
    "end_ns",
    "wall_time_ms",
    "cpu_time_ms",
    "kind",
    "status",
    "error",
    "attrs",
}


def _regenerate_python_golden() -> None:
    subprocess.run(
        ["uv", "run", "--group", "benchmark", "python", "benchmark-go/testdata/generate_golden.py"],
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), **_minimal_env()},
        check=True,
        capture_output=True,
    )


def _run_go_emitter(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["go", "run", "./cmd/golden", "-out", str(out_dir)],
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


def _span_by_name(trace: dict) -> dict[str, dict]:
    # In our fixture each span name is unique - safe to key by name.
    return {s["name"]: s for s in trace["spans"]}


def _assert_tree_shape_equal(py: dict, go: dict) -> None:
    # Root span in each: parent_id is null, trace_id matches child trace_ids.
    py_spans = py["spans"]
    go_spans = go["spans"]

    assert len(py_spans) == len(go_spans), (
        f"span count differs: py={len(py_spans)} go={len(go_spans)}"
    )

    # Both must declare one root.
    py_roots = [s for s in py_spans if s["parent_id"] is None]
    go_roots = [s for s in go_spans if s["parent_id"] is None]
    assert len(py_roots) == 1 and len(go_roots) == 1, (
        "exactly one root span expected per trace"
    )

    py_tid = py_roots[0]["trace_id"]
    go_tid = go_roots[0]["trace_id"]
    # All spans in each trace share the root trace_id.
    assert all(s["trace_id"] == py_tid for s in py_spans), "py trace_id not consistent"
    assert all(s["trace_id"] == go_tid for s in go_spans), "go trace_id not consistent"

    # Each non-root's parent_id refers to a real span_id.
    for s in py_spans:
        if s["parent_id"] is not None:
            assert any(t["span_id"] == s["parent_id"] for t in py_spans), (
                f"py parent_id {s['parent_id']} of {s['name']} not found"
            )
    for s in go_spans:
        if s["parent_id"] is not None:
            assert any(t["span_id"] == s["parent_id"] for t in go_spans), (
                f"go parent_id {s['parent_id']} of {s['name']} not found"
            )


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    if not shutil.which("go"):
        pytest.skip("go toolchain not installed")
    # Regenerate Python golden so local edits show up.
    _regenerate_python_golden()
    go_out = tmp_path_factory.mktemp("go_out")
    _run_go_emitter(go_out)
    py = _load(PY_GOLDEN)
    go = _load(go_out / "fixture_001.json")
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
    # trace_id values are random, but both must be 32-char hex.
    for src, t in (("py", py), ("go", go)):
        assert len(t["trace_id"]) == 32, f"{src} trace_id wrong length"
        int(t["trace_id"], 16)  # raises if not hex


def test_span_count_matches_fixture(traces):
    py, go = traces
    # Our fixture emits 5 spans: root + search + fetch + summarize + llm.
    assert len(py["spans"]) == 5
    assert len(go["spans"]) == 5


def test_tree_structure_matches(traces):
    py, go = traces
    _assert_tree_shape_equal(py, go)


def test_per_span_keys_match(traces):
    py, go = traces
    py_by = _span_by_name(py)
    go_by = _span_by_name(go)
    assert set(py_by) == set(go_by), (
        f"span name set mismatch: only-py={set(py_by) - set(go_by)} only-go={set(go_by) - set(py_by)}"
    )
    for name in py_by:
        pk = set(py_by[name].keys())
        gk = set(go_by[name].keys())
        assert pk == gk, (
            f"{name}: span field set differs: only-py={pk - gk} only-go={gk - pk}"
        )


def test_per_span_exact_fields_match(traces):
    py, go = traces
    py_by = _span_by_name(py)
    go_by = _span_by_name(go)
    for name, psp in py_by.items():
        gsp = go_by[name]
        for field in EXACT_MATCH_FIELDS:
            assert psp[field] == gsp[field], (
                f"{name}.{field} differs: py={psp[field]!r} go={gsp[field]!r}"
            )


def test_id_field_types_match(traces):
    py, go = traces
    py_by = _span_by_name(py)
    go_by = _span_by_name(go)
    for name in py_by:
        for field in ID_FIELDS:
            pv, gv = py_by[name][field], go_by[name][field]
            if pv is None:
                assert gv is None, f"{name}.{field} type differs: py=None go={gv!r}"
            else:
                assert isinstance(gv, str), f"{name}.{field} go value not a string"
                assert len(pv) == len(gv), (
                    f"{name}.{field} length differs: py={len(pv)} go={len(gv)}"
                )
