import type { AnalyticsSpan } from "../../types";

const KIND_COLORS: Record<string, string> = {
  root: "text-fg-primary bg-elevate",
  tool: "text-blue-300 bg-blue-900/20",
  llm: "text-pink-300 bg-pink-900/20",
  internal: "text-fg-muted bg-elevate",
};

function fmtMs(ms: number | null | undefined) {
  if (ms == null) return "—";
  if (ms < 1) return `${ms.toFixed(2)}ms`;
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export default function SpanList({
  spans,
  selectedSpanId,
  onSelect,
}: {
  spans: AnalyticsSpan[];
  selectedSpanId: string | null;
  onSelect: (span: AnalyticsSpan) => void;
}) {
  if (spans.length === 0) return null;
  return (
    <ul className="divide-y divide-border-soft border border-border-soft rounded-md overflow-hidden">
      {spans.map((s) => {
        const active = s.span_id === selectedSpanId;
        return (
          <li key={s.span_id}>
            <button
              onClick={() => onSelect(s)}
              className={
                "w-full text-left px-3 py-1.5 flex items-center gap-3 hover:bg-hover transition-colors " +
                (active ? "bg-elevate" : "")
              }
            >
              <span
                className={
                  "shrink-0 text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded " +
                  (KIND_COLORS[s.kind] ?? KIND_COLORS.internal)
                }
              >
                {s.kind}
              </span>
              <span className="text-[11px] font-mono truncate flex-1">{s.name}</span>
              <span className="shrink-0 text-[11px] tabular-nums text-fg-muted">
                wall {fmtMs(s.wall_time_ms)}
              </span>
              <span className="shrink-0 text-[11px] tabular-nums text-fg-muted">
                cpu {fmtMs(s.cpu_time_ms)}
              </span>
              {s.status === "error" && (
                <span className="shrink-0 text-[10px] text-error">error</span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
