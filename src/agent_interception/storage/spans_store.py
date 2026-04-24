"""Persistence for forwarded OTel-style spans.

Separate from InteractionStore so the analytics feature can evolve
independently of proxy interception. Same SQLite file, different tables
(ingested_spans, ingested_traces).

Contract with obs.py / obs.go: they POST JSON of the shape

  {
    "trace_id": "hex32",
    "config": "py" | "go" | null,
    "query_id": "q003" | null,
    "label": "optional human label",
    "spans": [ { name, trace_id, span_id, parent_id, start_ns, end_ns,
                 wall_time_ms, cpu_time_ms, kind, attrs, status, error },
               ... ]
  }

The payload shape mirrors benchmark/traces/*.json exactly, so CLI-run and
UI-forwarded traces are fungible.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class SpansStore:
    """Thin wrapper around the aiosqlite connection used by InteractionStore.

    We reuse the same DB file so there's a single source of truth and
    backups/purges are simpler.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    async def insert_trace(self, payload: dict[str, Any]) -> tuple[str, int]:
        """Insert all spans from a forwarded trace. Returns (trace_id, count).

        Idempotent on span_id - duplicate forwards are a no-op so agents can
        safely retry. trace_id is taken from the payload's top-level field if
        present, otherwise derived from the root span.
        """
        spans = payload.get("spans") or []
        if not spans:
            raise ValueError("payload has no spans[]")

        trace_id = payload.get("trace_id")
        if not trace_id:
            # Fall back to root span's trace_id.
            trace_id = spans[0].get("trace_id")
        if not trace_id:
            raise ValueError("cannot determine trace_id (no payload.trace_id and no span.trace_id)")

        config = payload.get("config")
        query_id = payload.get("query_id")
        label = payload.get("label")

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO ingested_traces (trace_id, config, query_id, label)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    config = COALESCE(excluded.config, ingested_traces.config),
                    query_id = COALESCE(excluded.query_id, ingested_traces.query_id),
                    label = COALESCE(excluded.label, ingested_traces.label)
                """,
                (trace_id, config, query_id, label),
            )
            inserted = 0
            for sp in spans:
                try:
                    await db.execute(
                        """
                        INSERT INTO ingested_spans (
                            span_id, trace_id, parent_id, name, kind,
                            start_ns, end_ns, wall_time_ms, cpu_time_ms,
                            attrs, status, error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(span_id) DO NOTHING
                        """,
                        (
                            sp["span_id"],
                            sp["trace_id"],
                            sp.get("parent_id"),
                            sp["name"],
                            sp.get("kind", "internal"),
                            int(sp["start_ns"]),
                            int(sp["end_ns"]),
                            float(sp["wall_time_ms"]),
                            float(sp["cpu_time_ms"]) if sp.get("cpu_time_ms") is not None else None,
                            json.dumps(sp.get("attrs") or {}),
                            sp.get("status", "ok"),
                            sp.get("error"),
                        ),
                    )
                    inserted += 1
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning("skipping malformed span: %s (%s)", e, sp.get("span_id"))
            await db.commit()
            return trace_id, inserted

    async def list_traces(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List forwarded traces with minimal aggregate info for the session list."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    t.trace_id, t.config, t.query_id, t.label, t.received_at,
                    COUNT(s.span_id) AS span_count,
                    MIN(s.start_ns) AS first_start_ns,
                    MAX(s.end_ns) AS last_end_ns,
                    SUM(CASE WHEN s.kind = 'llm' THEN s.wall_time_ms ELSE 0 END) AS llm_time_ms,
                    SUM(CASE WHEN s.kind = 'tool' THEN s.wall_time_ms ELSE 0 END) AS tool_time_ms,
                    SUM(CASE WHEN s.kind = 'llm' THEN 1 ELSE 0 END) AS llm_turns,
                    SUM(CASE WHEN s.kind = 'tool' THEN 1 ELSE 0 END) AS tool_calls
                FROM ingested_traces t
                LEFT JOIN ingested_spans s ON s.trace_id = t.trace_id
                GROUP BY t.trace_id
                ORDER BY t.received_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "trace_id": r["trace_id"],
                    "config": r["config"],
                    "query_id": r["query_id"],
                    "label": r["label"],
                    "received_at": r["received_at"],
                    "span_count": int(r["span_count"] or 0),
                    "total_wall_ms": (
                        float(r["last_end_ns"] - r["first_start_ns"]) / 1e6
                        if r["first_start_ns"] is not None
                        and r["last_end_ns"] is not None
                        else 0.0
                    ),
                    "llm_time_ms": float(r["llm_time_ms"] or 0),
                    "tool_time_ms": float(r["tool_time_ms"] or 0),
                    "llm_turns": int(r["llm_turns"] or 0),
                    "tool_calls": int(r["tool_calls"] or 0),
                }
                for r in rows
            ]

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Return one trace with all its spans ordered by start_ns."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            t_cursor = await db.execute(
                "SELECT trace_id, config, query_id, label, received_at "
                "FROM ingested_traces WHERE trace_id = ?",
                (trace_id,),
            )
            trow = await t_cursor.fetchone()
            if trow is None:
                return None
            s_cursor = await db.execute(
                """
                SELECT span_id, trace_id, parent_id, name, kind, start_ns, end_ns,
                       wall_time_ms, cpu_time_ms, attrs, status, error
                FROM ingested_spans
                WHERE trace_id = ?
                ORDER BY start_ns ASC
                """,
                (trace_id,),
            )
            srows = await s_cursor.fetchall()
            spans = [
                {
                    "span_id": r["span_id"],
                    "trace_id": r["trace_id"],
                    "parent_id": r["parent_id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "start_ns": int(r["start_ns"]),
                    "end_ns": int(r["end_ns"]),
                    "wall_time_ms": float(r["wall_time_ms"]),
                    "cpu_time_ms": (
                        float(r["cpu_time_ms"]) if r["cpu_time_ms"] is not None else None
                    ),
                    "attrs": json.loads(r["attrs"]),
                    "status": r["status"],
                    "error": r["error"],
                }
                for r in srows
            ]
            return {
                "trace_id": trow["trace_id"],
                "config": trow["config"],
                "query_id": trow["query_id"],
                "label": trow["label"],
                "received_at": trow["received_at"],
                "spans": spans,
            }

    async def list_metrics(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """Bulk per-session metrics for the all-sessions view.

        Runs the same per-trace aggregation as trace_metrics but across the
        most recent `limit` traces in one pass. Order: most recent first.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT trace_id FROM ingested_traces ORDER BY received_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
            trace_ids = [r["trace_id"] for r in rows]

        out: list[dict[str, Any]] = []
        for tid in trace_ids:
            m = await self.trace_metrics(tid)
            if m is not None and "error" not in m:
                out.append(m)
        return out

    async def trace_metrics(self, trace_id: str) -> dict[str, Any] | None:
        """Compute per-session metrics in the benchmark's shape."""
        trace = await self.get_trace(trace_id)
        if trace is None:
            return None
        spans = trace["spans"]
        if not spans:
            return {"trace_id": trace_id, "config": trace.get("config"), "error": "no spans"}

        roots = [s for s in spans if s["parent_id"] is None]
        if not roots:
            return {"trace_id": trace_id, "config": trace.get("config"), "error": "no root span"}
        root = roots[0]

        def sum_by_name(name: str) -> float:
            return sum(
                s["wall_time_ms"] for s in spans if s["name"] == name and s["parent_id"] is not None
            )

        tool_search_ms = sum_by_name("tool.search")
        tool_fetch_ms = sum_by_name("tool.fetch")
        tool_summarize_ms = sum_by_name("tool.summarize")
        tool_time_ms = tool_search_ms + tool_fetch_ms + tool_summarize_ms
        llm_time_ms = sum_by_name("llm.generate")
        retry_wait_ms = sum_by_name("llm.retry_wait")
        total_ms = root["wall_time_ms"]

        child_sum = sum(s["wall_time_ms"] for s in spans if s["parent_id"] == root["span_id"])
        gap_ms = max(total_ms - child_sum, 0.0)
        overhead_ms = max(
            total_ms - tool_time_ms - llm_time_ms - retry_wait_ms - gap_ms, 0.0
        )
        # active = real work; excludes both inter-turn pauses and 429 retry waits.
        active_ms = max(total_ms - gap_ms - retry_wait_ms, 0.0)

        num_tool_calls = sum(1 for s in spans if s["kind"] == "tool")
        # Count only non-rate-limited llm.generate attempts as turns.
        num_llm_turns = sum(
            1
            for s in spans
            if s["name"] == "llm.generate"
            and not bool(s["attrs"].get("llm.rate_limited", False))
        )
        num_retry_waits = sum(1 for s in spans if s["name"] == "llm.retry_wait")
        num_retries = sum(
            int(s["attrs"].get("tool.retry_count", 0)) for s in spans if s["kind"] == "tool"
        )
        num_parse_errors = sum(
            1
            for s in spans
            if s["kind"] == "llm" and bool(s["attrs"].get("llm.parse_error", False))
        )
        input_tokens_total = sum(
            int(s["attrs"].get("llm.input_tokens", 0)) for s in spans if s["kind"] == "llm"
        )
        output_tokens_total = sum(
            int(s["attrs"].get("llm.output_tokens", 0)) for s in spans if s["kind"] == "llm"
        )

        return {
            "trace_id": trace_id,
            "config": trace.get("config"),
            "query_id": trace.get("query_id"),
            "label": trace.get("label"),
            "total_latency_ms": total_ms,
            "active_latency_ms": active_ms,
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
            "input_tokens_total": input_tokens_total,
            "output_tokens_total": output_tokens_total,
        }
