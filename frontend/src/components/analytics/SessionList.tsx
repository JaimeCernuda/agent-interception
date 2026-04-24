import type { AnalyticsSessionSummary } from "../../types";

function fmtMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function fmtWhen(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export default function SessionList({
  sessions,
  selectedId,
  onSelect,
  loading,
}: {
  sessions: AnalyticsSessionSummary[];
  selectedId: string | null;
  onSelect: (traceId: string) => void;
  loading: boolean;
}) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 py-2 border-b border-border-soft flex items-center gap-2 shrink-0">
        <span className="text-xs font-medium text-fg-primary">Sessions</span>
        <span className="text-[10px] text-fg-muted">
          {loading ? "loading…" : `${sessions.length} total`}
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        {!loading && sessions.length === 0 && (
          <div className="p-3 text-[11px] text-fg-muted leading-relaxed">
            No forwarded traces yet. Run the benchmark with{" "}
            <code className="rounded bg-elevate px-1">--forward-to http://localhost:8080/api/spans</code>{" "}
            (Python) or{" "}
            <code className="rounded bg-elevate px-1">--forward-to http://localhost:8080/api/spans</code>{" "}
            (Go) to see sessions here.
          </div>
        )}
        <ul className="divide-y divide-border-soft">
          {sessions.map((s) => {
            const active = s.trace_id === selectedId;
            return (
              <li key={s.trace_id}>
                <button
                  onClick={() => onSelect(s.trace_id)}
                  className={
                    "w-full text-left px-3 py-2 hover:bg-hover transition-colors " +
                    (active ? "bg-elevate" : "")
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-medium truncate">
                      {s.label ?? s.query_id ?? s.trace_id.slice(0, 12)}
                    </span>
                    {s.config && (
                      <span className="text-[10px] uppercase tracking-wider text-fg-muted">
                        {s.config}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-fg-muted mt-0.5">
                    <span>{fmtMs(s.total_wall_ms)}</span>
                    <span>· {s.llm_turns} llm</span>
                    <span>· {s.tool_calls} tools</span>
                  </div>
                  <div className="text-[10px] text-fg-muted mt-0.5">{fmtWhen(s.received_at)}</div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
