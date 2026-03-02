import { useEffect, useState } from "react";
import { getSessions } from "../../api";
import type { SessionInfo } from "../../types";

interface Props {
  value: string | null;
  onChange: (sessionId: string | null) => void;
}

export default function SessionSelector({ value, onChange }: Props) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    getSessions()
      .then(setSessions)
      .catch(() => setSessions([]))
      .finally(() => setLoading(false));
  }, []);

  const filtered = search
    ? sessions.filter(
        (s) =>
          s.sessionId.toLowerCase().includes(search.toLowerCase()) ||
          s.providers.some((p) => p.includes(search)) ||
          s.models.some((m) => m.toLowerCase().includes(search.toLowerCase()))
      )
    : sessions;

  if (loading) {
    return <div className="text-gray-500 text-sm">Loading sessions…</div>;
  }

  if (sessions.length === 0) {
    return (
      <div className="text-gray-500 text-sm">
        No interactions found. Run the proxy and make some requests to see them here.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 w-full max-w-xl">
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Search sessions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-blue-600"
        />
        {value && (
          <button
            onClick={() => onChange(null)}
            className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1.5 border border-gray-700 rounded-lg"
          >
            Clear
          </button>
        )}
      </div>

      <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden max-h-48 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="text-gray-600 text-sm px-3 py-3">No sessions match.</div>
        ) : (
          filtered.map((s) => {
            const isUnsessioned = s.sessionId === "__unsessioned__";
            return (
              <button
                key={s.sessionId}
                onClick={() => onChange(s.sessionId)}
                className={`w-full text-left px-3 py-2.5 border-b border-gray-700/50 last:border-0 hover:bg-gray-700/50 transition-colors ${
                  value === s.sessionId ? "bg-blue-900/30 border-l-2 border-l-blue-500" : ""
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  {isUnsessioned ? (
                    <div className="text-xs text-yellow-400/80 italic truncate">
                      Unsessioned interactions
                    </div>
                  ) : (
                    <div className="font-mono text-xs text-blue-300 truncate">{s.sessionId}</div>
                  )}
                  <div className="text-xs text-gray-500 shrink-0">{s.interactionCount} calls</div>
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {s.providers.join(", ")}
                  {s.models.length > 0 && ` · ${s.models.slice(0, 2).join(", ")}${s.models.length > 2 ? "…" : ""}`}
                  {isUnsessioned && (
                    <span className="ml-1 text-gray-600">(no /_session/ prefix used)</span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
