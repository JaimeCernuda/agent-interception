import { useState } from "react";
import type { ClearScope } from "../types";

interface Props {
  onConfirm: (scope: ClearScope, sessionId?: string) => Promise<void>;
  onCancel: () => void;
  currentSessionId?: string | null;
}

const SCOPE_LABELS: Record<ClearScope, string> = {
  session: "Current session only",
  "24h": "Last 24 hours",
  all: "All interactions",
};

export default function ClearModal({ onConfirm, onCancel, currentSessionId }: Props) {
  const [scope, setScope] = useState<ClearScope>("all");
  const [loading, setLoading] = useState(false);

  const handleConfirm = async () => {
    setLoading(true);
    try {
      await onConfirm(scope, scope === "session" ? (currentSessionId ?? undefined) : undefined);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70" onClick={onCancel} />

      {/* Modal */}
      <div className="relative bg-elevate border border-border rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <div className="flex items-start gap-3 mb-4">
          <span className="text-red-400 text-xl mt-0.5">⚠</span>
          <div>
            <h2 className="text-base font-semibold text-fg-primary">Clear interaction data?</h2>
            <p className="text-sm text-fg-secondary mt-1">
              This action is destructive and cannot be undone.
            </p>
          </div>
        </div>

        <div className="mb-5">
          <div className="text-xs font-semibold uppercase tracking-wider text-fg-secondary mb-2">
            Scope
          </div>
          <div className="space-y-2">
            {(["session", "24h", "all"] as ClearScope[]).map((s) => (
              <label key={s} className="flex items-center gap-3 cursor-pointer">
                <input
                  type="radio"
                  name="scope"
                  value={s}
                  checked={scope === s}
                  onChange={() => setScope(s)}
                  disabled={s === "session" && !currentSessionId}
                  className="accent-red-500"
                />
                <span
                  className={`text-sm ${
                    s === "session" && !currentSessionId
                      ? "text-fg-muted"
                      : "text-fg-primary"
                  }`}
                >
                  {SCOPE_LABELS[s]}
                  {s === "session" && !currentSessionId && (
                    <span className="text-fg-muted ml-1">(no session selected)</span>
                  )}
                </span>
              </label>
            ))}
          </div>
        </div>

        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg border border-border text-fg-primary hover:text-fg-primary hover:border-fg-muted transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg bg-red-700 hover:bg-red-600 text-white font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {loading && <span className="animate-spin">⟳</span>}
            {loading ? "Clearing…" : "Clear interactions"}
          </button>
        </div>
      </div>
    </div>
  );
}
