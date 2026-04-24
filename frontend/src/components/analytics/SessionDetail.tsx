import { useEffect, useState } from "react";
import { getAnalyticsMetrics, getAnalyticsSession } from "../../api";
import type { AnalyticsMetrics, AnalyticsSession, AnalyticsSpan } from "../../types";
import StageBreakdownBar from "./StageBreakdownBar";
import SpanList from "./SpanList";

function fmtMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function MetricPill({ label, value, subtle }: { label: string; value: string; subtle?: boolean }) {
  return (
    <div className={"flex flex-col items-start px-3 py-2 rounded-md border border-border-soft " + (subtle ? "bg-elevate/30" : "bg-elevate")}>
      <span className="text-[10px] uppercase tracking-wider text-fg-muted">{label}</span>
      <span className="text-sm tabular-nums font-medium">{value}</span>
    </div>
  );
}

export default function SessionDetail({ traceId }: { traceId: string | null }) {
  const [trace, setTrace] = useState<AnalyticsSession | null>(null);
  const [metrics, setMetrics] = useState<AnalyticsMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedSpan, setSelectedSpan] = useState<AnalyticsSpan | null>(null);

  useEffect(() => {
    if (!traceId) {
      setTrace(null);
      setMetrics(null);
      setSelectedSpan(null);
      return;
    }
    let cancelled = false;
    setError(null);
    setSelectedSpan(null);
    Promise.all([getAnalyticsSession(traceId), getAnalyticsMetrics(traceId)])
      .then(([s, m]) => {
        if (cancelled) return;
        setTrace(s);
        setMetrics(m);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [traceId]);

  if (!traceId) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted text-xs">
        Select a session on the left.
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center text-error text-xs">
        {error}
      </div>
    );
  }

  if (!trace || !metrics) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted text-xs">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* Header */}
      <div className="space-y-1">
        <div className="flex items-baseline gap-3">
          <h2 className="text-sm font-semibold">
            {trace.label ?? trace.query_id ?? "session"}
          </h2>
          {trace.config && (
            <span className="text-[10px] uppercase tracking-wider text-fg-muted">
              config {trace.config}
            </span>
          )}
        </div>
        <div className="text-[11px] text-fg-muted font-mono break-all">
          trace {trace.trace_id}
        </div>
      </div>

      {/* Pills - pauses + retry waits excluded (don't distort results) */}
      <div className="flex flex-wrap gap-2">
        <MetricPill label="total" value={fmtMs(metrics.active_latency_ms)} />
        <MetricPill label="llm" value={fmtMs(metrics.llm_time_ms)} />
        <MetricPill label="tools" value={fmtMs(metrics.tool_time_ms)} />
        <MetricPill label="llm turns" value={String(metrics.num_llm_turns)} subtle />
        <MetricPill label="tool calls" value={String(metrics.num_tool_calls)} subtle />
        {metrics.num_retry_waits > 0 && (
          <MetricPill
            label={`retries (${metrics.num_retry_waits}) excluded`}
            value={fmtMs(metrics.retry_wait_ms)}
            subtle
          />
        )}
        <MetricPill label="in tokens" value={String(metrics.input_tokens_total)} subtle />
        <MetricPill label="out tokens" value={String(metrics.output_tokens_total)} subtle />
      </div>

      {/* Stage breakdown bar */}
      <div className="space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-fg-muted">
          Stage breakdown
        </div>
        <StageBreakdownBar metrics={metrics} />
      </div>

      {/* Span list */}
      <div className="space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-fg-muted">
          Spans ({trace.spans.length})
        </div>
        <SpanList
          spans={trace.spans}
          selectedSpanId={selectedSpan?.span_id ?? null}
          onSelect={setSelectedSpan}
        />
      </div>

      {/* Span attrs panel */}
      {selectedSpan && (
        <div className="space-y-1 border-t border-border-soft pt-3">
          <div className="text-[11px] uppercase tracking-wider text-fg-muted">
            {selectedSpan.name} — attributes
          </div>
          <pre className="text-[11px] font-mono bg-elevate rounded-md p-2 overflow-auto max-h-64 whitespace-pre-wrap break-words">
            {JSON.stringify(selectedSpan.attrs, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
