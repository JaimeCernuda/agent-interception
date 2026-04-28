"""Observability core for the benchmark.

One file, one responsibility: wrap OpenTelemetry's SDK so every call site in the
benchmark looks like `with obs.span("tool.fetch", url=u): ...`, and at the end of
each agent.query root span we flush the full trace to a JSON file under
benchmark/traces/<config>/<query_id>.json.

Design choices (see plan):
  - Real opentelemetry-sdk TracerProvider so attribute vocabulary stays OTel-compatible.
  - No OTLP, no collector. A single in-process SpanProcessor buffers spans per trace
    and hands them off to the Observer when the root span closes.
  - CPU time is tracked by hand (time.process_time_ns) and stored as `cpu_time_ms` in
    the emitted JSON. OTel's SDK does not capture process-CPU time natively.
  - span.kind ("root" | "tool" | "llm") is inferred from the span name so callers do
    not have to pass it.

This module is deliberately ~200 lines. If it grows, push helpers to span_schema.py
or analysis/, not here.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanProcessor
from opentelemetry.trace import Status, StatusCode

from benchmark.span_schema import (
    LLM_GENERATE,
    ROOT_SPAN,
    TOOL_FETCH,
    TOOL_SEARCH,
    TOOL_SUMMARIZE,
    SpanKind,
)

_CPU_ATTR = "_cpu_time_ms"  # kept as private attr; stripped from public span dict


# ---- Global tracer + collector, set up once per process ---------------------

_init_lock = threading.Lock()
_tracer: otel_trace.Tracer | None = None
_collector: "_TraceCollector | None" = None


class _TraceCollector(SpanProcessor):
    """Buffers finished spans in-memory, keyed by trace_id."""

    def __init__(self) -> None:
        self._by_trace: dict[str, list[ReadableSpan]] = defaultdict(list)
        self._lock = threading.Lock()

    def on_start(self, span, parent_context=None):  # type: ignore[override]
        return

    def on_end(self, span: ReadableSpan) -> None:  # type: ignore[override]
        tid = f"{span.context.trace_id:032x}"
        with self._lock:
            self._by_trace[tid].append(span)

    def shutdown(self) -> None:  # type: ignore[override]
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # type: ignore[override]
        return True

    def pop_trace(self, trace_id: str) -> list[ReadableSpan]:
        with self._lock:
            return self._by_trace.pop(trace_id, [])


def _init() -> None:
    global _tracer, _collector
    with _init_lock:
        if _tracer is not None:
            return
        provider = TracerProvider()
        _collector = _TraceCollector()
        provider.add_span_processor(_collector)
        otel_trace.set_tracer_provider(provider)
        _tracer = otel_trace.get_tracer("benchmark")


# ---- Public API --------------------------------------------------------------


def input_hash(x: Any) -> str:
    """Deterministic 16-hex-char digest of any JSON-serializable input."""
    if isinstance(x, bytes):
        data = x
    elif isinstance(x, str):
        data = x.encode("utf-8")
    else:
        data = json.dumps(x, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _kind_for(name: str) -> SpanKind:
    if name == ROOT_SPAN:
        return SpanKind.ROOT
    if name.startswith("llm."):
        return SpanKind.LLM
    if name.startswith("tool."):
        return SpanKind.TOOL
    return SpanKind.INTERNAL


class SpanHandle:
    """Thin wrapper so call sites do not need to import opentelemetry directly."""

    def __init__(self, span: otel_trace.Span) -> None:
        self._span = span

    def set(self, key: str, value: Any) -> None:
        """Set an attribute. Accepts OTel-compatible values; coerces dicts/lists via JSON."""
        if isinstance(value, (dict, list, tuple)) and not all(
            isinstance(v, (str, bool, int, float)) for v in (value.values() if isinstance(value, dict) else value)
        ):
            value = json.dumps(value, default=str)
        self._span.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, str(exc)))


class Observer:
    """One Observer per query. Holds the output directory and the active trace_id.

    If forward_to is set, the finished trace JSON is also POSTed there when the
    root span closes (in addition to being written to out_dir). Forward failures
    are logged but never abort the run - the local JSON is authoritative.
    """

    def __init__(
        self,
        config: str,
        query_id: str,
        out_dir: str | os.PathLike,
        forward_to: str | None = None,
        label: str | None = None,
    ) -> None:
        _init()
        self.config = config
        self.query_id = query_id
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.forward_to = forward_to
        self.label = label
        self._trace_id: str | None = None

    @contextmanager
    def root(self, name: str = ROOT_SPAN, **attrs: Any) -> Iterator[SpanHandle]:
        """Open the root span. On exit (after the root's on_end fires), flush the trace."""
        attrs = {
            "config": self.config,
            "query_id": self.query_id,
            "span.kind": SpanKind.ROOT.value,
            **attrs,
        }
        trace_id: str | None = None
        try:
            with self._span(name, attrs) as handle:
                trace_id = f"{handle._span.get_span_context().trace_id:032x}"
                self._trace_id = trace_id
                yield handle
        finally:
            if trace_id is not None:
                self._flush()

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[SpanHandle]:
        kind = _kind_for(name).value
        attrs = {"span.kind": kind, **attrs}
        with self._span(name, attrs) as handle:
            yield handle

    def emit_synthetic_span(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        cpu_start_ns: int | None = None,
        cpu_end_ns: int | None = None,
        **attrs: Any,
    ) -> None:
        """Record a span with explicit wall (and optionally CPU) timestamps.

        Used by configs that observe an external process whose call/return
        timing they want to attribute to a span without literally bracketing
        their own code with a context manager (e.g. claude-agent-sdk's
        message stream, where the LLM round-trip happens between yields).
        """
        assert _tracer is not None
        kind = _kind_for(name).value
        attrs = {"span.kind": kind, **attrs}
        otel_span = _tracer.start_span(name, start_time=int(start_ns))
        for k, v in attrs.items():
            SpanHandle(otel_span).set(k, v)
        if cpu_start_ns is not None and cpu_end_ns is not None:
            otel_span.set_attribute(_CPU_ATTR, max(0.0, (cpu_end_ns - cpu_start_ns) / 1e6))
        else:
            otel_span.set_attribute(_CPU_ATTR, 0.0)
        otel_span.end(end_time=int(end_ns))

    @contextmanager
    def _span(self, name: str, attrs: dict[str, Any]) -> Iterator[SpanHandle]:
        assert _tracer is not None
        cpu_start = time.process_time_ns()
        with _tracer.start_as_current_span(name) as otel_span:
            handle = SpanHandle(otel_span)
            for k, v in attrs.items():
                handle.set(k, v)
            try:
                yield handle
            except Exception as exc:
                handle.record_exception(exc)
                raise
            finally:
                cpu_ms = (time.process_time_ns() - cpu_start) / 1e6
                otel_span.set_attribute(_CPU_ATTR, cpu_ms)

    def _flush(self) -> None:
        assert self._trace_id is not None
        assert _collector is not None
        spans = _collector.pop_trace(self._trace_id)
        records = [_span_to_dict(s) for s in spans]
        records.sort(key=lambda r: r["start_ns"])
        out = {
            "trace_id": self._trace_id,
            "config": self.config,
            "query_id": self.query_id,
            "label": self.label,
            "spans": records,
        }
        path = self.out_dir / f"{self.query_id}.json"
        with path.open("w") as f:
            json.dump(out, f, indent=2, default=str)
        if self.forward_to:
            _forward_trace(self.forward_to, out)


def _forward_trace(url: str, payload: dict[str, Any]) -> None:
    """POST the finished trace to the analytics ingest endpoint.

    Best-effort: failures are logged via stderr but do not abort the run.
    The local JSON file on disk is the authoritative record.
    """
    import sys
    import urllib.error
    import urllib.request

    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                print(
                    f"obs: forward to {url} got status {resp.status}", file=sys.stderr
                )
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"obs: forward to {url} failed: {e}", file=sys.stderr)


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    parent = span.parent
    attrs = dict(span.attributes or {})
    cpu_ms = attrs.pop(_CPU_ATTR, None)
    kind = attrs.pop("span.kind", "internal")
    status = "error" if span.status.status_code == StatusCode.ERROR else "ok"
    return {
        "name": span.name,
        "trace_id": f"{ctx.trace_id:032x}",
        "span_id": f"{ctx.span_id:016x}",
        "parent_id": f"{parent.span_id:016x}" if parent else None,
        "start_ns": int(span.start_time or 0),
        "end_ns": int(span.end_time or 0),
        "wall_time_ms": ((span.end_time or 0) - (span.start_time or 0)) / 1e6,
        "cpu_time_ms": cpu_ms,
        "kind": kind,
        "attrs": attrs,
        "status": status,
        "error": span.status.description,
    }


# ---- Re-exports for call sites ----------------------------------------------

__all__ = [
    "Observer",
    "SpanHandle",
    "input_hash",
    "ROOT_SPAN",
    "TOOL_SEARCH",
    "TOOL_FETCH",
    "TOOL_SUMMARIZE",
    "LLM_GENERATE",
]
