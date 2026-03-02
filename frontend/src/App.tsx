import { useState, useCallback } from "react";
import InteractionsTable from "./components/InteractionsTable";
import InteractionDrawer from "./components/InteractionDrawer";
import ClearModal from "./components/ClearModal";
import Toast, { type ToastState } from "./components/ui/Toast";
import VisualizePage from "./pages/VisualizePage";
import { clearInteractions } from "./api";
import type { ClearScope } from "./types";

type Page = "interactions" | "visualize";

export default function App() {
  const [page, setPage] = useState<Page>("interactions");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showClearModal, setShowClearModal] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleClear = useCallback(
    async (scope: ClearScope, sessionId?: string) => {
      try {
        const result = await clearInteractions(scope, sessionId);
        setShowClearModal(false);
        setSelectedId(null);
        setRefreshKey((k) => k + 1);
        setToast({ type: "success", message: `Cleared ${result.deleted} interaction(s).` });
      } catch (e) {
        setToast({ type: "error", message: String(e) });
      }
    },
    []
  );

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-3 flex items-center gap-4">
        <span className="text-lg font-semibold tracking-tight shrink-0">Agent Interceptor</span>
        {/* Nav tabs */}
        <nav className="flex gap-1 ml-4">
          {(["interactions", "visualize"] as Page[]).map((p) => (
            <button
              key={p}
              onClick={() => setPage(p)}
              className={`px-3 py-1.5 text-sm rounded-md capitalize transition-colors ${
                page === p
                  ? "bg-gray-800 text-gray-100"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {p}
            </button>
          ))}
        </nav>
        <div className="flex-1" />
        {/* Clear button */}
        <button
          onClick={() => setShowClearModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-red-800/60 text-red-400 hover:bg-red-900/30 hover:border-red-700 transition-colors"
        >
          <span>⚠</span>
          Clear interactions
        </button>
      </header>

      <main className="p-4">
        {page === "interactions" && (
          <InteractionsTable
            key={refreshKey}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
        {page === "visualize" && <VisualizePage />}
      </main>

      {selectedId && page === "interactions" && (
        <InteractionDrawer id={selectedId} onClose={() => setSelectedId(null)} />
      )}

      {showClearModal && (
        <ClearModal
          onConfirm={handleClear}
          onCancel={() => setShowClearModal(false)}
        />
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
