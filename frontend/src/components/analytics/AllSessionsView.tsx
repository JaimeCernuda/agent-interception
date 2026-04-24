import { useEffect, useMemo, useState } from "react";
import { listAnalyticsMetrics } from "../../api";
import type { AnalyticsMetrics } from "../../types";

/**
 * Reproduces the benchmark's plot_2 inside the UI: one panel per config,
 * each showing a row of stacked bars (one per session). Rate-limit pauses
 * are excluded so the bar heights reflect actual work, not pacing.
 *
 * Pure SVG, no chart lib. visx is overkill for a grid of rectangles.
 */

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

const BAR_W = 22;
const BAR_GAP = 6;
const TOP_PAD = 20;
const BOTTOM_PAD = 40;
const LEFT_PAD = 50;
const RIGHT_PAD = 10;
const PLOT_H = 220;

function fmt(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function Panel({
  title,
  metrics,
  yMax,
  onHover,
}: {
  title: string;
  metrics: AnalyticsMetrics[];
  yMax: number;
  onHover: (m: AnalyticsMetrics | null) => void;
}) {
  const plotW = metrics.length * (BAR_W + BAR_GAP) + BAR_GAP;
  const svgW = plotW + LEFT_PAD + RIGHT_PAD;
  const svgH = PLOT_H + TOP_PAD + BOTTOM_PAD;
  const yScale = (v: number) => (v / yMax) * PLOT_H;

  // Y-axis ticks: 0, yMax/2, yMax
  const yTicks = [0, yMax / 2, yMax];

  return (
    <div className="flex flex-col min-w-0">
      <div className="text-[11px] font-medium mb-1 px-2">{title}</div>
      <div className="overflow-x-auto">
        <svg width={svgW} height={svgH} className="block">
          {/* y-axis grid */}
          {yTicks.map((t) => {
            const y = TOP_PAD + PLOT_H - yScale(t);
            return (
              <g key={t}>
                <line
                  x1={LEFT_PAD}
                  x2={LEFT_PAD + plotW}
                  y1={y}
                  y2={y}
                  stroke="currentColor"
                  strokeOpacity={0.1}
                />
                <text
                  x={LEFT_PAD - 6}
                  y={y + 3}
                  fontSize={9}
                  textAnchor="end"
                  fill="currentColor"
                  fillOpacity={0.6}
                >
                  {fmt(t)}
                </text>
              </g>
            );
          })}
          {/* bars */}
          {metrics.map((m, i) => {
            const x = LEFT_PAD + BAR_GAP + i * (BAR_W + BAR_GAP);
            let yCursor = TOP_PAD + PLOT_H;
            return (
              <g
                key={m.trace_id}
                onMouseEnter={() => onHover(m)}
                onMouseLeave={() => onHover(null)}
                style={{ cursor: "default" }}
              >
                {SEGMENTS.map((seg) => {
                  const v = Number(m[seg as keyof AnalyticsMetrics]) || 0;
                  if (v <= 0) return null;
                  const h = yScale(v);
                  yCursor -= h;
                  return (
                    <rect
                      key={seg}
                      x={x}
                      y={yCursor}
                      width={BAR_W}
                      height={h}
                      fill={COLORS[seg]}
                    />
                  );
                })}
                <text
                  x={x + BAR_W / 2}
                  y={TOP_PAD + PLOT_H + 12}
                  fontSize={8}
                  textAnchor="end"
                  transform={`rotate(-60 ${x + BAR_W / 2} ${TOP_PAD + PLOT_H + 12})`}
                  fill="currentColor"
                  fillOpacity={0.7}
                >
                  {m.query_id ?? m.trace_id.slice(0, 6)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] px-2">
      {SEGMENTS.map((seg) => (
        <div key={seg} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-sm"
            style={{ backgroundColor: COLORS[seg] }}
          />
          <span className="text-fg-muted">{LABELS[seg]}</span>
        </div>
      ))}
    </div>
  );
}

function HoverPill({ m }: { m: AnalyticsMetrics | null }) {
  if (!m) {
    return (
      <div className="text-[11px] text-fg-muted h-[18px]">
        hover a bar to see breakdown
      </div>
    );
  }
  return (
    <div className="text-[11px] flex flex-wrap gap-x-3 gap-y-0.5 h-auto">
      <span className="font-medium">{m.query_id ?? m.trace_id.slice(0, 10)}</span>
      {m.config && <span className="text-fg-muted">{m.config}</span>}
      <span className="tabular-nums">total {fmt(m.active_latency_ms)}</span>
      <span className="tabular-nums">llm {fmt(m.llm_time_ms)}</span>
      <span className="tabular-nums">fetch {fmt(m.tool_fetch_ms)}</span>
      <span className="tabular-nums">sum {fmt(m.tool_summarize_ms)}</span>
      <span className="tabular-nums">turns {m.num_llm_turns}</span>
      <span className="tabular-nums">tools {m.num_tool_calls}</span>
    </div>
  );
}

function SummaryTable({ groups }: { groups: Array<{ config: string; metrics: AnalyticsMetrics[] }> }) {
  const rows = groups.map((g) => {
    const n = g.metrics.length;
    const mean = (get: (m: AnalyticsMetrics) => number) =>
      n > 0 ? g.metrics.reduce((a, m) => a + get(m), 0) / n : 0;
    return {
      config: g.config,
      n,
      active: mean((m) => m.active_latency_ms),
      llm: mean((m) => m.llm_time_ms),
      fetch: mean((m) => m.tool_fetch_ms),
      summarize: mean((m) => m.tool_summarize_ms),
      turns: mean((m) => m.num_llm_turns),
      tools: mean((m) => m.num_tool_calls),
    };
  });
  return (
    <div className="rounded-md border border-border-soft overflow-hidden">
      <table className="w-full text-[11px]">
        <thead className="bg-elevate text-fg-muted">
          <tr>
            <th className="text-left px-2 py-1">config</th>
            <th className="text-right px-2 py-1">n</th>
            <th className="text-right px-2 py-1">mean active</th>
            <th className="text-right px-2 py-1">mean llm</th>
            <th className="text-right px-2 py-1">mean fetch</th>
            <th className="text-right px-2 py-1">mean summarize</th>
            <th className="text-right px-2 py-1">mean turns</th>
            <th className="text-right px-2 py-1">mean tools</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.config} className="border-t border-border-soft">
              <td className="px-2 py-1 font-medium">{r.config}</td>
              <td className="px-2 py-1 text-right tabular-nums">{r.n}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.active)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.llm)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.fetch)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.summarize)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{r.turns.toFixed(1)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{r.tools.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type DedupeMode = "all" | "latest-per-query";

export default function AllSessionsView() {
  const [all, setAll] = useState<AnalyticsMetrics[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<AnalyticsMetrics | null>(null);
  const [dedupe, setDedupe] = useState<DedupeMode>("latest-per-query");

  useEffect(() => {
    let cancelled = false;
    const run = () => {
      listAnalyticsMetrics({ limit: 500 })
        .then((m) => {
          if (!cancelled) {
            setAll(m);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    };
    run();
    const t = window.setInterval(run, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  const { groups, yMax } = useMemo(() => {
    // Optional dedupe: keep the most recent session per (config, query_id).
    // The list endpoint returns most-recent first, so first-seen wins.
    let rows = all;
    if (dedupe === "latest-per-query") {
      const seen = new Set<string>();
      rows = [];
      for (const m of all) {
        const key = `${m.config ?? "_"}|${m.query_id ?? m.trace_id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        rows.push(m);
      }
    }

    // Group by config, sort each group by query_id for stable x-axis across configs.
    const byConfig = new Map<string, AnalyticsMetrics[]>();
    for (const m of rows) {
      const cfg = m.config ?? "(no config)";
      if (!byConfig.has(cfg)) byConfig.set(cfg, []);
      byConfig.get(cfg)!.push(m);
    }
    const sortedGroups = Array.from(byConfig.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([config, metrics]) => ({
        config,
        metrics: metrics
          .slice()
          .sort((a, b) =>
            (a.query_id ?? a.trace_id).localeCompare(b.query_id ?? b.trace_id)
          ),
      }));

    const max = Math.max(1, ...rows.map((m) => m.active_latency_ms));
    return { groups: sortedGroups, yMax: max };
  }, [all, dedupe]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted text-xs">
        Loading…
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
  if (groups.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted text-xs">
        No sessions yet. Forward some traces first.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-fg-muted">View</span>
        <div className="flex gap-1">
          {(["latest-per-query", "all"] as DedupeMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setDedupe(m)}
              className={
                "px-2 py-1 text-[10px] rounded-md transition-colors " +
                (dedupe === m
                  ? "bg-elevate text-fg-primary"
                  : "text-fg-muted hover:text-fg-primary hover:bg-hover")
              }
            >
              {m === "latest-per-query" ? "latest per query" : "all sessions"}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <span className="text-[11px] text-fg-muted">active latency only · pauses excluded</span>
      </div>

      <HoverPill m={hover} />

      <Legend />

      <div className="space-y-4">
        {groups.map((g) => (
          <Panel
            key={g.config}
            title={`Config ${g.config}  ·  ${g.metrics.length} session${g.metrics.length === 1 ? "" : "s"}`}
            metrics={g.metrics}
            yMax={yMax}
            onHover={setHover}
          />
        ))}
      </div>

      <SummaryTable groups={groups} />
    </div>
  );
}
