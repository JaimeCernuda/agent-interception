import { useCallback, useEffect, useState } from "react";
import { listAnalyticsSessions } from "../api";
import type { AnalyticsSessionSummary } from "../types";
import SessionList from "../components/analytics/SessionList";
import SessionDetail from "../components/analytics/SessionDetail";
import AllSessionsView from "../components/analytics/AllSessionsView";

const POLL_INTERVAL_MS = 5000;

type Mode = "single" | "all";

export default function AnalyticsPage() {
  const [mode, setMode] = useState<Mode>("single");
  const [sessions, setSessions] = useState<AnalyticsSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await listAnalyticsSessions({ limit: 200 });
      setSessions(s);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  // Auto-select first session when none selected and list becomes available.
  useEffect(() => {
    if (selectedId == null && sessions.length > 0) {
      setSelectedId(sessions[0].trace_id);
    }
  }, [sessions, selectedId]);

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-2 border-b border-border-soft flex items-center gap-3 shrink-0">
        <span className="text-xs font-medium">Analytics</span>
        <nav className="flex items-center gap-1 ml-2">
          {(["single", "all"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={
                "px-2 py-1 text-[11px] rounded-md transition-colors " +
                (mode === m
                  ? "bg-elevate text-fg-primary"
                  : "text-fg-muted hover:text-fg-primary hover:bg-hover")
              }
            >
              {m === "single" ? "Per session" : "All sessions"}
            </button>
          ))}
        </nav>
        <span className="text-[10px] text-fg-muted">
          {mode === "single"
            ? "per-session breakdown"
            : "all sessions side by side (benchmark plot_2 style)"}
        </span>
        <div className="flex-1" />
        {error && <span className="text-[10px] text-error">{error}</span>}
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1 rounded-md bg-elevate hover:bg-hover text-fg-secondary"
        >
          Refresh
        </button>
      </div>
      {mode === "single" ? (
        <div className="flex-1 flex min-h-0">
          <aside className="w-72 border-r border-border-soft overflow-hidden shrink-0">
            <SessionList
              sessions={sessions}
              selectedId={selectedId}
              onSelect={setSelectedId}
              loading={loading}
            />
          </aside>
          <main className="flex-1 min-w-0 flex flex-col">
            <SessionDetail traceId={selectedId} />
          </main>
        </div>
      ) : (
        <div className="flex-1 flex min-h-0">
          <AllSessionsView />
        </div>
      )}
    </div>
  );
}
