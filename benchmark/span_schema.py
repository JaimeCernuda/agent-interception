"""Span record shape produced by obs.py and consumed by analysis/.

The JSON files written to benchmark/traces/<config>/<query_id>.json have this shape.
Attribute names follow OpenTelemetry semantic-convention style (dotted keys) so the
same records could be re-emitted through a real OTel collector later without rename.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SpanKind(str, Enum):
    ROOT = "root"
    TOOL = "tool"
    LLM = "llm"
    INTERNAL = "internal"


# Canonical span names emitted by the benchmark.
ROOT_SPAN = "agent.query"
TOOL_SEARCH = "tool.search"
TOOL_FETCH = "tool.fetch"
TOOL_SUMMARIZE = "tool.summarize"
LLM_GENERATE = "llm.generate"


@dataclass
class SpanRecord:
    """One span as read back from a trace JSON file."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start_ns: int
    end_ns: int
    wall_time_ms: float
    cpu_time_ms: float | None
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpanRecord:
        return cls(
            name=d["name"],
            trace_id=d["trace_id"],
            span_id=d["span_id"],
            parent_id=d.get("parent_id"),
            start_ns=int(d["start_ns"]),
            end_ns=int(d["end_ns"]),
            wall_time_ms=float(d["wall_time_ms"]),
            cpu_time_ms=(float(d["cpu_time_ms"]) if d.get("cpu_time_ms") is not None else None),
            kind=d.get("kind", "internal"),
            attrs=dict(d.get("attrs", {})),
            status=d.get("status", "ok"),
            error=d.get("error"),
        )


@dataclass
class TraceFile:
    """Top-level JSON shape: one file per query."""

    trace_id: str
    config: str        # "A" | "B" | "C"
    query_id: str
    spans: list[SpanRecord]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceFile:
        return cls(
            trace_id=d["trace_id"],
            config=d["config"],
            query_id=d["query_id"],
            spans=[SpanRecord.from_dict(s) for s in d["spans"]],
        )

    def root(self) -> SpanRecord:
        roots = [s for s in self.spans if s.parent_id is None]
        assert len(roots) == 1, f"expected 1 root span, found {len(roots)}"
        return roots[0]
