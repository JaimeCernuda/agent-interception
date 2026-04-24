import { useMemo } from "react";
import type { AnalyticsMetrics } from "../../types";

/**
 * Horizontal stacked bar showing how a session's active latency decomposes
 * into tool.search / tool.fetch / tool.summarize / llm.generate / overhead.
 *
 * Rendering is pure SVG - no chart lib needed for a single row, keeps the
 * component small and easy to defend in the thesis. If we need multi-session
 * side-by-side, promote to visx.
 */

// rate_limit_pause_ms is intentionally excluded: pacing sleeps distort results
// and live between spans. We show active latency only.
const COLORS = {
  tool_search_ms: "#6b7280",
  tool_fetch_ms: "#3b82f6",
  tool_summarize_ms: "#92400e",
  llm_time_ms: "#ec4899",
  framework_overhead_ms: "#d1d5db",
} as const;

const LABELS = {
  tool_search_ms: "tool.search",
  tool_fetch_ms: "tool.fetch",
  tool_summarize_ms: "tool.summarize",
  llm_time_ms: "llm.generate",
  framework_overhead_ms: "overhead",
} as const;

const SEGMENTS: Array<keyof typeof COLORS> = [
  "tool_search_ms",
  "tool_fetch_ms",
  "tool_summarize_ms",
  "llm_time_ms",
  "framework_overhead_ms",
];

function fmt(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export default function StageBreakdownBar({ metrics }: { metrics: AnalyticsMetrics }) {
  const { segments, total } = useMemo(() => {
    const segs = SEGMENTS.map((key) => ({
      key,
      value: Number(metrics[key as keyof AnalyticsMetrics]) || 0,
    })).filter((s) => s.value > 0);
    const sum = segs.reduce((a, s) => a + s.value, 0);
    return { segments: segs, total: sum };
  }, [metrics]);

  if (total <= 0) {
    return <div className="text-xs text-fg-muted">no timing data</div>;
  }

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] text-fg-muted">total (active, no pauses)</span>
        <span className="text-[11px] text-fg-muted tabular-nums">{fmt(metrics.active_latency_ms)}</span>
      </div>
      <div className="flex h-6 w-full overflow-hidden rounded-md border border-border-soft">
        {segments.map((s) => (
          <div
            key={s.key}
            style={{
              width: `${(s.value / total) * 100}%`,
              backgroundColor: COLORS[s.key],
            }}
            title={`${LABELS[s.key]}: ${fmt(s.value)} (${((s.value / total) * 100).toFixed(1)}%)`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
        {segments.map((s) => (
          <div key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: COLORS[s.key] }}
            />
            <span className="text-fg-muted">{LABELS[s.key]}</span>
            <span className="tabular-nums text-fg-primary">{fmt(s.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
