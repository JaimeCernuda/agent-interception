import { useEffect, useState } from "react";
import { getSessionGraph, getSessionToolSequence } from "../api";
import type { SessionGraph, TimelineEntry, ToolCallStep } from "../types";
import SessionSelector from "../components/visualize/SessionSelector";
import GraphView from "../components/visualize/GraphView";
import TimelineScrubber from "../components/visualize/TimelineScrubber";
import MetricsPanel from "../components/visualize/MetricsPanel";
import ToolSequence from "../components/visualize/ToolSequence";

export default function VisualizePage() {
  const [sessionId, setSessionId] = useState<string | null>(() => {
    // Restore from URL on mount
    const params = new URLSearchParams(window.location.search);
    return params.get("session");
  });
  const [graph, setGraph] = useState<SessionGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<{ from: string | null; to: string | null }>({
    from: null,
    to: null,
  });
  const [filterNodeId, setFilterNodeId] = useState<string | null>(null);
  const [toolSequence, setToolSequence] = useState<ToolCallStep[] | null>(null);
  const [toolSeqLoading, setToolSeqLoading] = useState(false);

  // Sync sessionId to URL
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (sessionId) {
      params.set("session", sessionId);
    } else {
      params.delete("session");
    }
    const newUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, "", newUrl);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setGraph(null);
      return;
    }
    setLoading(true);
    setError(null);
    setTimeRange({ from: null, to: null });
    setFilterNodeId(null);
    setToolSequence(null);
    setToolSeqLoading(true);

    Promise.all([
      getSessionGraph(sessionId),
      getSessionToolSequence(sessionId),
    ])
      .then(([g, seq]) => {
        setGraph(g);
        setToolSequence(seq);
        setLoading(false);
        setToolSeqLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
        setToolSeqLoading(false);
      });
  }, [sessionId]);

  // Filter timeline by time range
  const filteredTimeline: TimelineEntry[] = graph
    ? graph.timeline.filter((t) => {
        if (timeRange.from && t.timestamp < timeRange.from) return false;
        if (timeRange.to && t.timestamp > timeRange.to) return false;
        return true;
      })
    : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-base font-semibold text-gray-200 mb-3">Session visualizer</h1>
        <SessionSelector value={sessionId} onChange={setSessionId} />
        <p className="text-xs text-gray-600 mt-2">
          Named sessions are created when requests go through{" "}
          <code className="font-mono bg-gray-800 px-1 py-0.5 rounded text-gray-400">
            /_session/&#123;id&#125;/
          </code>
          {" "}— e.g. by setting{" "}
          <code className="font-mono bg-gray-800 px-1 py-0.5 rounded text-gray-400">
            ANTHROPIC_BASE_URL=http://localhost:8080/_session/my-run
          </code>
          . Other proxied requests appear under{" "}
          <span className="text-yellow-400/70 italic">Unsessioned interactions</span>.
        </p>
      </div>

      {loading && (
        <div className="text-gray-500 text-sm py-8 text-center">Loading graph…</div>
      )}

      {error && (
        <div className="text-red-400 text-sm py-4">Error: {error}</div>
      )}

      {!sessionId && !loading && (
        <div className="text-gray-600 text-sm py-8 text-center">
          Select a session above to see the call graph.
        </div>
      )}

      {graph && !loading && (
        <div className="space-y-6">
          {graph.nodes.length === 0 ? (
            <div className="text-gray-600 text-sm py-4">No interactions found for this session.</div>
          ) : (
            <div className="grid grid-cols-1 xl:grid-cols-[1fr_220px] gap-6 items-start">
              <div className="space-y-4 min-w-0">
                {/* Graph */}
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                    Call graph
                    {filterNodeId && (
                      <button
                        className="ml-2 normal-case text-blue-400 hover:underline"
                        onClick={() => setFilterNodeId(null)}
                      >
                        (clear filter)
                      </button>
                    )}
                  </div>
                  <GraphView
                    nodes={graph.nodes}
                    edges={graph.edges}
                    toolSequence={toolSequence ?? []}
                    onNodeClick={(id) => setFilterNodeId((prev) => (prev === id ? null : id))}
                  />
                </div>

                {/* Timeline */}
                <div>
                  <TimelineScrubber
                    entries={filteredTimeline.length > 0 ? filteredTimeline : graph.timeline}
                    onRangeChange={(from, to) => setTimeRange({ from, to })}
                  />
                </div>

                {/* Tool call sequence */}
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                    Tool call sequence
                    {toolSequence && toolSequence.length > 0 && (
                      <span className="ml-2 normal-case font-normal text-gray-600">
                        ({toolSequence.reduce((n, s) => n + s.toolCalls.length, 0)} calls across {toolSequence.length} interactions)
                      </span>
                    )}
                  </div>
                  {toolSeqLoading ? (
                    <div className="text-gray-600 text-xs py-2">Loading…</div>
                  ) : (
                    <ToolSequence steps={toolSequence ?? []} />
                  )}
                </div>

                {/* Filtered interactions table */}
                {(timeRange.from || filterNodeId) && filteredTimeline.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                      Filtered interactions ({filteredTimeline.length})
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-gray-800 text-gray-500">
                            <th className="text-left py-1.5 px-2 font-medium">Time</th>
                            <th className="text-left py-1.5 px-2 font-medium">Provider</th>
                            <th className="text-center py-1.5 px-2 font-medium">Status</th>
                            <th className="text-right py-1.5 px-2 font-medium">Latency</th>
                            <th className="text-left py-1.5 px-2 font-medium">ID</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredTimeline.slice(0, 50).map((t) => (
                            <tr key={t.interactionId} className="border-b border-gray-800/40">
                              <td className="py-1.5 px-2 font-mono text-gray-400">
                                {new Date(t.timestamp).toLocaleTimeString()}
                              </td>
                              <td className="py-1.5 px-2 text-gray-400">{t.provider}</td>
                              <td className={`py-1.5 px-2 text-center font-mono ${
                                t.status && t.status >= 400 ? "text-red-400" : "text-green-400"
                              }`}>
                                {t.status ?? "—"}
                              </td>
                              <td className="py-1.5 px-2 text-right font-mono text-gray-400">
                                {t.latencyMs != null ? `${t.latencyMs.toFixed(0)}ms` : "—"}
                              </td>
                              <td className="py-1.5 px-2 font-mono text-gray-600 truncate max-w-[100px]">
                                {t.interactionId.slice(0, 8)}…
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {filteredTimeline.length > 50 && (
                        <div className="text-gray-600 text-xs px-2 py-1">
                          Showing first 50 of {filteredTimeline.length}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Metrics panel */}
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                  Metrics
                </div>
                <MetricsPanel
                  graph={graph}
                  filteredCount={
                    timeRange.from || timeRange.to ? filteredTimeline.length : undefined
                  }
                  toolSequence={toolSequence ?? []}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
