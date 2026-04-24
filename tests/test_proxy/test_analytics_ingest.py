"""Integration test for the span ingest endpoint + analytics read endpoints.

Uses Starlette's TestClient to exercise the routes without a live HTTP server.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_interception.config import InterceptorConfig
from agent_interception.proxy.server import create_app


@pytest.fixture()
def app(tmp_path: Path):
    db = tmp_path / "test.db"
    cfg = InterceptorConfig(db_path=str(db))
    with TestClient(create_app(cfg)) as client:
        yield client


def _fixture_payload() -> dict:
    # Mirrors the benchmark golden trace shape.
    return {
        "trace_id": "a" * 32,
        "config": "py",
        "query_id": "q999",
        "label": "integration-test",
        "spans": [
            {
                "name": "agent.query",
                "trace_id": "a" * 32,
                "span_id": "0000000000000001",
                "parent_id": None,
                "start_ns": 1_000_000_000,
                "end_ns": 2_000_000_000,
                "wall_time_ms": 1000.0,
                "cpu_time_ms": 0.5,
                "kind": "root",
                "attrs": {"config": "py", "query_id": "q999", "query_text": "hello"},
                "status": "ok",
                "error": None,
            },
            {
                "name": "tool.search",
                "trace_id": "a" * 32,
                "span_id": "0000000000000002",
                "parent_id": "0000000000000001",
                "start_ns": 1_000_001_000,
                "end_ns": 1_050_000_000,
                "wall_time_ms": 49.999,
                "cpu_time_ms": 0.001,
                "kind": "tool",
                "attrs": {"tool.name": "static", "tool.retry_count": 0, "tool.num_results": 2},
                "status": "ok",
                "error": None,
            },
            {
                "name": "llm.generate",
                "trace_id": "a" * 32,
                "span_id": "0000000000000003",
                "parent_id": "0000000000000001",
                "start_ns": 1_050_001_000,
                "end_ns": 1_900_000_000,
                "wall_time_ms": 849.999,
                "cpu_time_ms": 0.1,
                "kind": "llm",
                "attrs": {
                    "llm.model": "test",
                    "llm.provider": "anthropic",
                    "llm.input_tokens": 100,
                    "llm.output_tokens": 50,
                    "llm.parse_error": False,
                },
                "status": "ok",
                "error": None,
            },
        ],
    }


def test_ingest_then_list(app: TestClient):
    resp = app.post("/api/spans", json=_fixture_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["spans_inserted"] == 3
    assert body["trace_id"] == "a" * 32

    sessions = app.get("/api/analytics/sessions").json()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["trace_id"] == "a" * 32
    assert s["config"] == "py"
    assert s["query_id"] == "q999"
    assert s["span_count"] == 3
    assert s["llm_turns"] == 1
    assert s["tool_calls"] == 1


def test_ingest_idempotent(app: TestClient):
    app.post("/api/spans", json=_fixture_payload())
    # Second ingest with same span_ids should not double-insert.
    app.post("/api/spans", json=_fixture_payload())
    sessions = app.get("/api/analytics/sessions").json()
    assert sessions[0]["span_count"] == 3


def test_get_session_and_metrics(app: TestClient):
    app.post("/api/spans", json=_fixture_payload())
    trace_id = "a" * 32

    detail = app.get(f"/api/analytics/sessions/{trace_id}").json()
    assert detail["trace_id"] == trace_id
    assert detail["config"] == "py"
    assert detail["label"] == "integration-test"
    assert len(detail["spans"]) == 3
    # Spans must be ordered by start_ns.
    starts = [s["start_ns"] for s in detail["spans"]]
    assert starts == sorted(starts)
    # Attrs must round-trip as a dict (not a JSON string).
    root = next(s for s in detail["spans"] if s["parent_id"] is None)
    assert root["attrs"]["query_text"] == "hello"

    metrics = app.get(f"/api/analytics/sessions/{trace_id}/metrics").json()
    assert metrics["trace_id"] == trace_id
    assert metrics["num_llm_turns"] == 1
    assert metrics["num_tool_calls"] == 1
    assert metrics["input_tokens_total"] == 100
    assert metrics["output_tokens_total"] == 50
    # LLM time fraction should dominate (~850 / 900 active).
    assert metrics["llm_time_fraction"] > 0.9


def test_session_not_found(app: TestClient):
    resp = app.get("/api/analytics/sessions/deadbeef")
    assert resp.status_code == 404


def test_ingest_rejects_empty_spans(app: TestClient):
    resp = app.post("/api/spans", json={"trace_id": "bad", "spans": []})
    assert resp.status_code == 400


def test_ingest_derives_trace_id_from_root(app: TestClient):
    payload = _fixture_payload()
    payload.pop("trace_id")  # force fallback path
    resp = app.post("/api/spans", json=payload)
    assert resp.status_code == 201
    assert resp.json()["trace_id"] == "a" * 32


def test_full_obs_forward_roundtrip(app: TestClient, monkeypatch, tmp_path):
    """Run the real Observer with forward_to pointing at the test app.

    Uses monkeypatch to swap urllib.urlopen with a function that calls the
    TestClient - so we never need a live server.
    """
    import urllib.request

    # Wire the Observer's forward call through the test client.
    def fake_urlopen(req, timeout=10):
        data = req.data.decode("utf-8") if req.data else ""
        resp = app.post("/api/spans", content=data, headers={"Content-Type": "application/json"})
        # Minimal fake response that obs.py checks (.status attr).
        class R:
            status = resp.status_code
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from benchmark.obs import Observer

    obs = Observer(
        config="py",
        query_id="forward_test",
        out_dir=str(tmp_path),
        forward_to="http://test/api/spans",
        label="forward-smoke",
    )
    with obs.root(query_text="hello via obs"):
        with obs.span("tool.search", **{"tool.name": "static", "tool.retry_count": 0}) as s:
            s.set("tool.num_results", 1)
        with obs.span("llm.generate", **{"llm.model": "x", "llm.provider": "anthropic"}) as s:
            s.set("llm.input_tokens", 10)
            s.set("llm.output_tokens", 5)
            s.set("llm.parse_error", False)

    sessions = app.get("/api/analytics/sessions").json()
    assert any(s["label"] == "forward-smoke" for s in sessions), sessions
    forwarded = [s for s in sessions if s["label"] == "forward-smoke"][0]
    assert forwarded["span_count"] == 3  # root + tool + llm
    assert forwarded["config"] == "py"
    assert forwarded["query_id"] == "forward_test"
