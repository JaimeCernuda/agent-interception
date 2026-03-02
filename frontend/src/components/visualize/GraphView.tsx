import { useState, useCallback } from "react";
import type { GraphNode, GraphEdge, ToolCallStep } from "../../types";

const NODE_W = 130;
const NODE_H = 44;
const COL_GAP = 160;
const ROW_GAP = 64;
const PADDING = 32;

// Column order by type
const TYPE_ORDER: Record<GraphNode["type"], number> = {
  agent: 0,
  tool: 1,
  proxy: 2,
  provider: 3,
  model: 4,
};

const TYPE_COLORS: Record<GraphNode["type"], { fill: string; stroke: string; text: string }> = {
  agent:    { fill: "#1e3a5f", stroke: "#3b82f6", text: "#93c5fd" },
  tool:     { fill: "#3b1f00", stroke: "#f97316", text: "#fdba74" },
  proxy:    { fill: "#1a2e1a", stroke: "#22c55e", text: "#86efac" },
  provider: { fill: "#2d1b4e", stroke: "#a855f7", text: "#d8b4fe" },
  model:    { fill: "#1e3030", stroke: "#14b8a6", text: "#5eead4" },
};

function latencyColor(avgMs: number | null): string {
  if (avgMs == null) return "#374151";
  if (avgMs < 500) return "#166534";
  if (avgMs < 2000) return "#854d0e";
  return "#7f1d1d";
}

function edgeColor(errorRate: number): string {
  if (errorRate > 0.1) return "#ef4444";
  if (errorRate > 0.01) return "#f59e0b";
  return "#22c55e";
}

function edgeWidth(callCount: number): number {
  return Math.min(8, Math.max(1.5, 1.5 + Math.log10(Math.max(1, callCount)) * 2));
}

function fmt(ms: number | null) {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(0)}ms`;
}

interface Layout {
  node: GraphNode;
  x: number;
  y: number;
}

function computeLayout(nodes: GraphNode[]): Layout[] {
  // Group by column
  const cols: Map<number, GraphNode[]> = new Map();
  for (const n of nodes) {
    const col = TYPE_ORDER[n.type];
    if (!cols.has(col)) cols.set(col, []);
    cols.get(col)!.push(n);
  }

  const layout: Layout[] = [];
  const colNums = Array.from(cols.keys()).sort();

  for (const col of colNums) {
    const group = cols.get(col)!;
    const x = PADDING + col * (NODE_W + COL_GAP);
    const totalH = group.length * NODE_H + (group.length - 1) * (ROW_GAP - NODE_H);
    const startY = PADDING + Math.max(0, (200 - totalH) / 2);
    group.forEach((node, i) => {
      layout.push({ node, x, y: startY + i * ROW_GAP });
    });
  }
  return layout;
}

interface SeqNode extends Layout {
  stepIndex: number;
  callIndex: number;
  isParallel: boolean;
}

interface SeqEdge {
  fromId: string;
  toId: string;
  color: string;
  width: number;
  dashed: boolean;
  callCount: number;
  errorRate: number;
  avgLatencyMs: number | null;
  p95LatencyMs: number | null;
  totalTokens: number;
  totalCostUsd: number;
}

interface ParallelRect {
  x: number;
  y: number;
  w: number;
  h: number;
  stepIndex: number;
}

interface SeqLayout {
  seqNodes: SeqNode[];
  baseLayout: Layout[];
  seqEdges: SeqEdge[];
  parallelRects: ParallelRect[];
  totalCols: number;
}

function computeSequentialLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  toolSequence: ToolCallStep[]
): SeqLayout {
  const activeSteps = toolSequence.filter((s) => s.toolCalls.length > 0);

  // Build base layout for non-tool nodes
  const nonToolNodes = nodes.filter((n) => n.type !== "tool");
  const agentNode = nonToolNodes.find((n) => n.type === "agent");
  const proxyNode = nonToolNodes.find((n) => n.type === "proxy");
  const providerNodes = nonToolNodes.filter((n) => n.type === "provider");
  const modelNodes = nonToolNodes.filter((n) => n.type === "model");

  // Column assignments:
  // col 0: agent
  // col 1..N: tool steps
  // col N+1: proxy
  // col N+2: providers
  // col N+3: models
  const N = activeSteps.length;
  const proxyCol = N + 1;
  const providerCol = N + 2;
  const modelCol = N + 3;

  const baseLayout: Layout[] = [];

  const placeGroup = (group: GraphNode[], col: number) => {
    if (group.length === 0) return;
    const x = PADDING + col * (NODE_W + COL_GAP);
    const totalH = group.length * NODE_H + (group.length - 1) * (ROW_GAP - NODE_H);
    const startY = PADDING + Math.max(0, (200 - totalH) / 2);
    group.forEach((node, i) => {
      baseLayout.push({ node, x, y: startY + i * ROW_GAP });
    });
  };

  if (agentNode) placeGroup([agentNode], 0);
  placeGroup(proxyNode ? [proxyNode] : [], proxyCol);
  placeGroup(providerNodes, providerCol);
  placeGroup(modelNodes, modelCol);

  // Build sequential tool nodes
  const seqNodes: SeqNode[] = [];
  const parallelRects: ParallelRect[] = [];

  activeSteps.forEach((step, si) => {
    const col = si + 1;
    const x = PADDING + col * (NODE_W + COL_GAP);
    const count = step.toolCalls.length;
    const totalH = count * NODE_H + (count - 1) * (ROW_GAP - NODE_H);
    const startY = PADDING + Math.max(0, (200 - totalH) / 2);
    const isParallel = count > 1;

    step.toolCalls.forEach((tc, j) => {
      const id = `seq-${si}-${tc.name}-${j}`;
      const y = startY + j * ROW_GAP;
      seqNodes.push({
        node: {
          id,
          type: "tool",
          label: tc.name ?? "?",
          metrics: {
            callCount: 1,
            errorRate: 0,
            avgLatencyMs: null,
            p95LatencyMs: null,
            totalTokens: 0,
            totalCostUsd: 0,
          },
        },
        x,
        y,
        stepIndex: si,
        callIndex: j,
        isParallel,
      });
    });

    if (isParallel) {
      const rectPad = 8;
      const rectY = startY - rectPad;
      const rectH = totalH + NODE_H + rectPad * 2;
      parallelRects.push({
        x: x - rectPad,
        y: rectY,
        w: NODE_W + rectPad * 2,
        h: rectH,
        stepIndex: si,
      });
    }
  });

  // Build sequential edges
  const seqEdges: SeqEdge[] = [];

  // agent → tools in step 0
  if (agentNode && activeSteps.length > 0) {
    const step0Nodes = seqNodes.filter((n) => n.stepIndex === 0);
    for (const tn of step0Nodes) {
      seqEdges.push({
        fromId: agentNode.id,
        toId: tn.node.id,
        color: "#22c55e",
        width: 1.5,
        dashed: false,
        callCount: 1,
        errorRate: 0,
        avgLatencyMs: null,
        p95LatencyMs: null,
        totalTokens: 0,
        totalCostUsd: 0,
      });
    }
  }

  // step i → step i+1 sequential arrows
  for (let i = 0; i < activeSteps.length - 1; i++) {
    const fromNodes = seqNodes.filter((n) => n.stepIndex === i);
    const toNodes = seqNodes.filter((n) => n.stepIndex === i + 1);
    const M = fromNodes.length;
    const K = toNodes.length;

    // Cap fan-out for large parallel groups
    if (M <= 3 && K <= 3) {
      // draw all M*K edges
      for (const fn of fromNodes) {
        for (const tn of toNodes) {
          seqEdges.push({
            fromId: fn.node.id,
            toId: tn.node.id,
            color: "#4b5563",
            width: 1.5,
            dashed: true,
            callCount: 1,
            errorRate: 0,
            avgLatencyMs: null,
            p95LatencyMs: null,
            totalTokens: 0,
            totalCostUsd: 0,
          });
        }
      }
    } else {
      // each tool in step i → first tool in step i+1
      for (const fn of fromNodes) {
        if (toNodes[0]) {
          seqEdges.push({
            fromId: fn.node.id,
            toId: toNodes[0].node.id,
            color: "#4b5563",
            width: 1.5,
            dashed: true,
            callCount: 1,
            errorRate: 0,
            avgLatencyMs: null,
            p95LatencyMs: null,
            totalTokens: 0,
            totalCostUsd: 0,
          });
        }
      }
      // last tool in step i → each tool in step i+1
      const lastFrom = fromNodes[fromNodes.length - 1];
      if (lastFrom) {
        for (let ki = 1; ki < toNodes.length; ki++) {
          seqEdges.push({
            fromId: lastFrom.node.id,
            toId: toNodes[ki].node.id,
            color: "#4b5563",
            width: 1.5,
            dashed: true,
            callCount: 1,
            errorRate: 0,
            avgLatencyMs: null,
            p95LatencyMs: null,
            totalTokens: 0,
            totalCostUsd: 0,
          });
        }
      }
    }
  }

  // last step tools → proxy
  if (proxyNode && activeSteps.length > 0) {
    const lastStepNodes = seqNodes.filter((n) => n.stepIndex === activeSteps.length - 1);
    for (const fn of lastStepNodes) {
      seqEdges.push({
        fromId: fn.node.id,
        toId: proxyNode.id,
        color: "#22c55e",
        width: 1.5,
        dashed: false,
        callCount: 1,
        errorRate: 0,
        avgLatencyMs: null,
        p95LatencyMs: null,
        totalTokens: 0,
        totalCostUsd: 0,
      });
    }
  }

  // Keep proxy → provider and provider → model edges from original graph.edges
  for (const e of edges) {
    const fromNode = nodes.find((n) => n.id === e.from);
    const toNode = nodes.find((n) => n.id === e.to);
    if (!fromNode || !toNode) continue;
    if (
      (fromNode.type === "proxy" && toNode.type === "provider") ||
      (fromNode.type === "provider" && toNode.type === "model")
    ) {
      seqEdges.push({
        fromId: e.from,
        toId: e.to,
        color: edgeColor(e.errorRate),
        width: edgeWidth(e.callCount),
        dashed: false,
        callCount: e.callCount,
        errorRate: e.errorRate,
        avgLatencyMs: e.avgLatencyMs,
        p95LatencyMs: e.p95LatencyMs,
        totalTokens: e.totalTokens,
        totalCostUsd: e.totalCostUsd,
      });
    }
  }

  return { seqNodes, baseLayout, seqEdges, parallelRects, totalCols: modelCol };
}

function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const cx1 = x1 + (x2 - x1) * 0.45;
  const cx2 = x1 + (x2 - x1) * 0.55;
  return `M ${x1} ${y1} C ${cx1} ${y1} ${cx2} ${y2} ${x2} ${y2}`;
}

interface TooltipData {
  x: number;
  y: number;
  content: React.ReactNode;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  toolSequence?: ToolCallStep[];
  onNodeClick?: (nodeId: string) => void;
  onEdgeClick?: (edge: GraphEdge) => void;
}

export default function GraphView({ nodes, edges, toolSequence, onNodeClick, onEdgeClick }: Props) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const activeSteps = toolSequence?.filter((s) => s.toolCalls.length > 0) ?? [];
  const useSeq = activeSteps.length > 0;

  const seqResult = useSeq
    ? computeSequentialLayout(nodes, edges, toolSequence!)
    : null;

  const layout = useSeq ? seqResult!.baseLayout : computeLayout(nodes);
  const allLayoutNodes: Array<{ node: GraphNode; x: number; y: number }> = useSeq
    ? [...layout, ...seqResult!.seqNodes]
    : layout;

  const maxX = Math.max(...allLayoutNodes.map((l) => l.x + NODE_W), 0);
  const maxY = Math.max(...allLayoutNodes.map((l) => l.y + NODE_H), 0);
  const svgW = maxX + PADDING * 2;
  const svgH = maxY + PADDING * 2;

  const posMap: Map<string, { cx: number; cy: number }> = new Map();
  for (const l of allLayoutNodes) {
    posMap.set(l.node.id, {
      cx: l.x + NODE_W / 2,
      cy: l.y + NODE_H / 2,
    });
  }

  const handleNodeEnter = useCallback(
    (_e: React.MouseEvent<SVGGElement>, node: GraphNode) => {
      const pos = posMap.get(node.id)!;

      // Sequential node tooltip
      if (node.id.startsWith("seq-") && seqResult) {
        const parts = node.id.split("-");
        const stepIndex = parseInt(parts[1], 10);
        const callIndex = parseInt(parts[parts.length - 1], 10);
        const step = activeSteps[stepIndex];
        const tc = step?.toolCalls[callIndex];
        const isParallel = step && step.toolCalls.length > 1;
        const stepLabel = `Step ${stepIndex + 1} of ${activeSteps.length}${isParallel ? " · parallel batch" : ""}`;

        let inputPreview: Array<[string, string]> = [];
        if (tc?.input) {
          inputPreview = Object.entries(tc.input)
            .slice(0, 2)
            .map(([k, v]) => [k, String(v).slice(0, 40)]);
        }

        setTooltip({
          x: pos.cx + 10,
          y: pos.cy - NODE_H,
          content: (
            <div className="text-xs space-y-0.5">
              <div className="font-semibold text-gray-100">{node.label}</div>
              <div className="text-gray-400">{stepLabel}</div>
              {inputPreview.length > 0 && (
                <>
                  <div className="border-t border-gray-700 my-1" />
                  {inputPreview.map(([k, v]) => (
                    <div key={k} className="text-gray-400">
                      <span className="text-gray-300">{k}:</span> {v}
                    </div>
                  ))}
                </>
              )}
            </div>
          ),
        });
        return;
      }

      setTooltip({
        x: pos.cx + 10,
        y: pos.cy - NODE_H,
        content: (
          <div className="text-xs space-y-0.5">
            <div className="font-semibold text-gray-100">{node.label}</div>
            <div className="text-gray-400 capitalize">{node.type}</div>
            <div className="border-t border-gray-700 my-1" />
            <div>Calls: <span className="text-gray-200">{node.metrics.callCount}</span></div>
            <div>Avg latency: <span className="text-gray-200">{fmt(node.metrics.avgLatencyMs)}</span></div>
            <div>p95 latency: <span className="text-gray-200">{fmt(node.metrics.p95LatencyMs)}</span></div>
            <div>Tokens: <span className="text-gray-200">{node.metrics.totalTokens.toLocaleString()}</span></div>
            <div>Cost: <span className="text-gray-200">${node.metrics.totalCostUsd.toFixed(4)}</span></div>
            <div>Error rate: <span className={node.metrics.errorRate > 0.05 ? "text-red-400" : "text-gray-200"}>
              {(node.metrics.errorRate * 100).toFixed(1)}%
            </span></div>
          </div>
        ),
      });
    },
    [posMap, seqResult, activeSteps]
  );

  const handleEdgeEnter = useCallback(
    (_e: React.MouseEvent, fromId: string, toId: string, edgeData: {
      callCount: number; errorRate: number; avgLatencyMs: number | null;
      p95LatencyMs: number | null; totalTokens: number; totalCostUsd: number;
    }) => {
      const fromPos = posMap.get(fromId);
      const toPos = posMap.get(toId);
      if (!fromPos || !toPos) return;
      const mx = (fromPos.cx + toPos.cx) / 2;
      const my = (fromPos.cy + toPos.cy) / 2;
      setTooltip({
        x: mx + 10,
        y: my - 60,
        content: (
          <div className="text-xs space-y-0.5">
            <div className="font-semibold text-gray-100">{fromId} → {toId}</div>
            <div className="border-t border-gray-700 my-1" />
            <div>Calls: <span className="text-gray-200">{edgeData.callCount}</span></div>
            <div>Avg latency: <span className="text-gray-200">{fmt(edgeData.avgLatencyMs)}</span></div>
            <div>p95 latency: <span className="text-gray-200">{fmt(edgeData.p95LatencyMs)}</span></div>
            <div>Tokens: <span className="text-gray-200">{edgeData.totalTokens.toLocaleString()}</span></div>
            <div>Cost: <span className="text-gray-200">${edgeData.totalCostUsd.toFixed(4)}</span></div>
            <div>Error rate: <span className={edgeData.errorRate > 0.05 ? "text-red-400" : "text-gray-200"}>
              {(edgeData.errorRate * 100).toFixed(1)}%
            </span></div>
          </div>
        ),
      });
    },
    [posMap]
  );

  const renderNode = (node: GraphNode, x: number, y: number) => {
    const colors = TYPE_COLORS[node.type];
    const bgColor = latencyColor(node.metrics.avgLatencyMs);
    return (
      <g
        key={node.id}
        className="cursor-pointer"
        onMouseEnter={(e) => handleNodeEnter(e, node)}
        onMouseLeave={() => setTooltip(null)}
        onClick={() => onNodeClick?.(node.id)}
      >
        <rect
          x={x}
          y={y}
          width={NODE_W}
          height={NODE_H}
          rx={8}
          fill={colors.fill}
          stroke={colors.stroke}
          strokeWidth={1.5}
        />
        {/* Latency indicator bar at bottom */}
        <rect
          x={x + 4}
          y={y + NODE_H - 5}
          width={NODE_W - 8}
          height={3}
          rx={1.5}
          fill={bgColor}
          opacity={0.6}
        />
        <text
          x={x + NODE_W / 2}
          y={y + 16}
          textAnchor="middle"
          fontSize={11}
          fontWeight="600"
          fill={colors.text}
        >
          {node.label.length > 14 ? node.label.slice(0, 13) + "…" : node.label}
        </text>
        <text
          x={x + NODE_W / 2}
          y={y + 30}
          textAnchor="middle"
          fontSize={9}
          fill="#6b7280"
        >
          {node.metrics.callCount} calls · {fmt(node.metrics.avgLatencyMs)}
        </text>
      </g>
    );
  };

  return (
    <div className="relative overflow-x-auto">
      <svg
        width={svgW}
        height={svgH}
        className="rounded bg-gray-900/50 border border-gray-800 min-w-full"
        onMouseLeave={() => setTooltip(null)}
      >
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#4b5563" />
          </marker>
          {["#22c55e", "#f59e0b", "#ef4444", "#4b5563"].map((color) => (
            <marker
              key={color}
              id={`arrow-${color.replace("#", "")}`}
              markerWidth="8"
              markerHeight="6"
              refX="7"
              refY="3"
              orient="auto"
            >
              <polygon points="0 0, 8 3, 0 6" fill={color} />
            </marker>
          ))}
        </defs>

        {useSeq && seqResult ? (
          <>
            {/* Parallel grouping rects */}
            {seqResult.parallelRects.map((pr) => (
              <g key={`prect-${pr.stepIndex}`}>
                <rect
                  x={pr.x}
                  y={pr.y}
                  width={pr.w}
                  height={pr.h}
                  rx={10}
                  fill="#3b1f00"
                  fillOpacity={0.4}
                  stroke="#f97316"
                  strokeOpacity={0.3}
                  strokeWidth={1}
                />
                <text
                  x={pr.x + pr.w / 2}
                  y={pr.y - 4}
                  textAnchor="middle"
                  fontSize={9}
                  fill="#f97316"
                  fillOpacity={0.7}
                >
                  parallel
                </text>
              </g>
            ))}

            {/* Sequential edges */}
            {seqResult.seqEdges.map((se, i) => {
              const from = posMap.get(se.fromId);
              const to = posMap.get(se.toId);
              if (!from || !to) return null;
              const markerId = `arrow-${se.color.replace("#", "")}`;
              const x1 = from.cx + NODE_W / 2 - 2;
              const y1 = from.cy;
              const x2 = to.cx - NODE_W / 2 + 10;
              const y2 = to.cy;
              return (
                <path
                  key={i}
                  d={edgePath(x1, y1, x2, y2)}
                  stroke={se.color}
                  strokeWidth={se.width}
                  strokeDasharray={se.dashed ? "4 3" : undefined}
                  fill="none"
                  opacity={0.7}
                  markerEnd={`url(#${markerId})`}
                  className="cursor-pointer hover:opacity-100 transition-opacity"
                  onMouseEnter={(e) => handleEdgeEnter(e, se.fromId, se.toId, se)}
                  onMouseLeave={() => setTooltip(null)}
                />
              );
            })}

            {/* Base (non-tool) nodes */}
            {seqResult.baseLayout.map(({ node, x, y }) => renderNode(node, x, y))}

            {/* Sequential tool nodes */}
            {seqResult.seqNodes.map(({ node, x, y }) => renderNode(node, x, y))}
          </>
        ) : (
          <>
            {/* Aggregate view edges */}
            {edges.map((edge, i) => {
              const from = posMap.get(edge.from);
              const to = posMap.get(edge.to);
              if (!from || !to) return null;
              const color = edgeColor(edge.errorRate);
              const markerId = `arrow-${color.replace("#", "")}`;
              const x1 = from.cx + NODE_W / 2 - 2;
              const y1 = from.cy;
              const x2 = to.cx - NODE_W / 2 + 10;
              const y2 = to.cy;
              return (
                <path
                  key={i}
                  d={edgePath(x1, y1, x2, y2)}
                  stroke={color}
                  strokeWidth={edgeWidth(edge.callCount)}
                  fill="none"
                  opacity={0.7}
                  markerEnd={`url(#${markerId})`}
                  className="cursor-pointer hover:opacity-100 transition-opacity"
                  onMouseEnter={(e) => handleEdgeEnter(e, edge.from, edge.to, edge)}
                  onMouseLeave={() => setTooltip(null)}
                  onClick={() => onEdgeClick?.(edge)}
                />
              );
            })}

            {/* Aggregate view nodes */}
            {layout.map(({ node, x, y }) => renderNode(node, x, y))}
          </>
        )}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-20 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 shadow-xl pointer-events-none"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          {tooltip.content}
        </div>
      )}

      {/* Legend */}
      <div className="flex gap-4 mt-2 text-xs text-gray-500 flex-wrap">
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#22c55e" }} /> Low error</span>
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#f59e0b" }} /> Some errors</span>
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#ef4444" }} /> High error</span>
        <span className="flex items-center gap-1 ml-2"><span className="inline-block w-3 h-3 rounded border border-gray-500" style={{ background: "#166534" }} /> Fast</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded border border-gray-500" style={{ background: "#854d0e" }} /> Slow</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded border border-gray-500" style={{ background: "#7f1d1d" }} /> Very slow</span>
        {useSeq && (
          <span className="flex items-center gap-1 ml-2">
            <span className="inline-block w-6 h-1 rounded border border-orange-700/40" style={{ background: "#3b1f00" }} />
            Parallel batch
          </span>
        )}
      </div>
    </div>
  );
}
