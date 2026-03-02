import type { SessionGraph, ToolCallStep } from "../../types";

function fmt(ms: number | null) {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(0)}ms`;
}

function pct(rate: number) {
  return `${(rate * 100).toFixed(1)}%`;
}

interface Props {
  graph: SessionGraph;
  filteredCount?: number;
  toolSequence?: ToolCallStep[];
}

export default function MetricsPanel({ graph, filteredCount, toolSequence }: Props) {
  const timeline = graph.timeline;
  const total = timeline.length;
  const errors = timeline.filter((t) => t.error || (t.status != null && t.status >= 400)).length;
  const latencies = timeline.map((t) => t.latencyMs).filter((l): l is number => l != null);
  const sortedLat = [...latencies].sort((a, b) => a - b);
  const avgLat = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;
  const p95Lat = sortedLat.length ? sortedLat[Math.floor(sortedLat.length * 0.95)] : null;
  const errorRate = total ? errors / total : 0;

  // Sum from edges for tokens/cost
  const agentProxyEdge = graph.edges.find((e) => e.from === "agent" && e.to === "proxy");
  const totalTokens = agentProxyEdge?.totalTokens ?? 0;
  const totalCost = agentProxyEdge?.totalCostUsd ?? 0;

  const streaming = timeline.filter((t) => t.isStreaming).length;
  const toolCallCount = toolSequence?.reduce((n, s) => n + s.toolCalls.length, 0) ?? null;

  const cards = [
    { label: "LLM turns", value: filteredCount != null ? `${filteredCount} / ${total}` : String(total) },
    { label: "Tool calls", value: toolCallCount != null ? String(toolCallCount) : "—" },
    { label: "Avg latency", value: fmt(avgLat) },
    { label: "p95 latency", value: fmt(p95Lat) },
    { label: "Error rate", value: pct(errorRate), warn: errorRate > 0.05 },
    { label: "Total tokens", value: totalTokens.toLocaleString() },
    { label: "Total cost", value: `$${totalCost.toFixed(4)}` },
    { label: "Streaming", value: `${streaming} / ${total}` },
  ];

  return (
    <div className="grid grid-cols-2 gap-2">
      {cards.map((c) => (
        <div key={c.label} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
          <div className="text-xs text-gray-500 mb-1">{c.label}</div>
          <div className={`text-sm font-semibold ${c.warn ? "text-red-400" : "text-gray-100"}`}>
            {c.value}
          </div>
        </div>
      ))}
    </div>
  );
}
