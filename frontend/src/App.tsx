import { useCallback, useEffect, useState } from "react";
import WorkspacePage from "./pages/WorkspacePage";
import InteractionsTable from "./components/InteractionsTable";
import InteractionDrawer from "./components/InteractionDrawer";
import ClearModal from "./components/ClearModal";
import Toast, { type ToastState } from "./components/ui/Toast";
import ErrorBoundary from "./components/ErrorBoundary";
import { clearInteractions } from "./api";
import type { ClearScope } from "./types";

type Theme = "dark" | "light";

function getInitialTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  return "dark";
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [showClearModal, setShowClearModal] = useState(false);
  const [showRawLog, setShowRawLog] = useState(false);
  const [rawSelectedId, setRawSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "light" ? "dark" : "light"));
  }, []);

  const handleClear = useCallback(async (scope: ClearScope, sessionId?: string) => {
    try {
      const result = await clearInteractions(scope, sessionId);
      setShowClearModal(false);
      setRawSelectedId(null);
      setRefreshKey((k) => k + 1);
      setToast({ type: "success", message: `Cleared ${result.deleted} interaction(s).` });
    } catch (e) {
      setToast({ type: "error", message: String(e) });
    }
  }, []);

  return (
    <div className="h-screen flex flex-col bg-canvas text-fg-primary">
      <header className="border-b border-border-soft px-4 py-2 flex items-center gap-3 shrink-0">
        <span className="text-base font-semibold tracking-tight">Agent Interceptor</span>
        <span className="text-[10px] uppercase tracking-widest text-fg-muted">Workspace</span>

        <div className="flex-1" />

        <button
          onClick={toggleTheme}
          className="p-1.5 rounded-md text-fg-muted hover:text-fg-primary hover:bg-elevate transition-colors"
          title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          {theme === "light" ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="12" r="5"/>
              <line x1="12" y1="1" x2="12" y2="3" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="12" y1="21" x2="12" y2="23" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="1" y1="12" x2="3" y2="12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="21" y1="12" x2="23" y2="12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
          )}
        </button>

        <button
          onClick={() => setShowClearModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-error/50 text-error hover:bg-error/10 transition-colors"
        >
          <span>⚠</span>
          Clear
        </button>
      </header>

      <main className="flex-1 min-h-0">
        <ErrorBoundary label="Workspace">
          <WorkspacePage onOpenRawLog={() => setShowRawLog(true)} />
        </ErrorBoundary>
      </main>

      {showRawLog && (
        <RawLogModal
          refreshKey={refreshKey}
          selectedId={rawSelectedId}
          onSelect={setRawSelectedId}
          onClose={() => { setShowRawLog(false); setRawSelectedId(null); }}
        />
      )}

      {showClearModal && (
        <ClearModal onConfirm={handleClear} onCancel={() => setShowClearModal(false)} />
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function RawLogModal({
  refreshKey,
  selectedId,
  onSelect,
  onClose,
}: {
  refreshKey: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-40 bg-canvas/95 backdrop-blur-sm flex flex-col">
      <div className="border-b border-border-soft px-4 py-2 flex items-center gap-3">
        <span className="text-sm font-semibold text-fg-primary">Raw interaction log</span>
        <div className="flex-1" />
        <button
          onClick={onClose}
          className="px-3 py-1 text-xs rounded-md bg-elevate hover:bg-hover text-fg-secondary"
        >Close</button>
      </div>
      <div className="flex-1 overflow-auto p-4">
        <InteractionsTable key={refreshKey} selectedId={selectedId} onSelect={onSelect} />
      </div>
      {selectedId && (
        <InteractionDrawer id={selectedId} onClose={() => onSelect(null)} />
      )}
    </div>
  );
}
