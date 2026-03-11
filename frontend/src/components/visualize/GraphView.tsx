import { useState, useCallback } from "react";
import type { GraphNode, GraphEdge, ToolCallStep } from "../../types";

// ─── Aggregate view constants ─────────────────────────────────────────────────
const NODE_W = 130;
const NODE_H = 44;
const COL_GAP = 160;
const ROW_GAP = 64;
const PADDING = 32;

const TYPE_ORDER: Record<GraphNode["type"], number> = {
  agent: 0, tool: 1, proxy: 2, provider: 3, model: 4,
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

interface AggLayout { node: GraphNode; x: number; y: number }

function computeLayout(nodes: GraphNode[]): AggLayout[] {
  const cols: Map<number, GraphNode[]> = new Map();
  for (const n of nodes) {
    const col = TYPE_ORDER[n.type];
    if (!cols.has(col)) cols.set(col, []);
    cols.get(col)!.push(n);
  }
  const layout: AggLayout[] = [];
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

// ─── New sequential view constants ───────────────────────────────────────────
const LLM_W = 170;
const LLM_H = 90;
const LLM_CALL_H = 44;
const TOOL_W = 140;
const TOOL_H = 66;
const AGENT_W = 130;
const AGENT_H = 44;
const H_GAP = 55;
const V_GAP = 14;
const SEQ_PAD = 32;

interface NewLLMCard { step: ToolCallStep; x: number; y: number; stepNum: number }
interface NewToolNode { id: string; name: string; x: number; y: number; hasResult: boolean; resultContent: string | null; isResultError: boolean; stepIndex: number }
interface NewEdge { x1: number; y1: number; x2: number; y2: number; color: string; dashed: boolean }
interface NewSeqLayout {
  llmCards: NewLLMCard[];
  toolGroups: Array<{ nodes: NewToolNode[]; stepIndex: number; parallelCount: number }>;
  agentX: number; agentY: number;
  edges: NewEdge[];
  svgW: number; svgH: number; centerY: number;
}

function isToolResultError(content: string): boolean {
  // Only inspect the start of the result — real tool errors appear immediately.
  // File content often contains words like "error", "exception", "failed" so
  // we avoid generic single-word matches.
  const head = content.slice(0, 100).toLowerCase().trimStart();
  return (
    head.startsWith("error:") ||
    head.startsWith("error ") ||
    head.includes("does not exist") ||
    head.includes("no such file") ||
    head.includes("permission denied") ||
    head.includes("not found.") ||
    head.startsWith("traceback (")
  );
}

function computeNewSeqLayout(toolSequence: ToolCallStep[]): NewSeqLayout {
  // Build a global map of all tool results across all steps so we can match
  // them regardless of how many steps later the result appears.
  const globalResultMap = new Map<string, string>();
  for (const step of toolSequence) {
    for (const r of step.toolResults) {
      if (r.toolCallId) globalResultMap.set(r.toolCallId, r.content);
    }
  }

  const maxParallel = Math.max(1, ...toolSequence.map(s => s.toolCalls.length));
  const maxToolsH = maxParallel * TOOL_H + Math.max(0, maxParallel - 1) * V_GAP;
  const contentH = Math.max(LLM_H, AGENT_H, maxToolsH);
  const svgH = SEQ_PAD * 2 + contentH;
  const centerY = SEQ_PAD + contentH / 2;

  const agentX = SEQ_PAD;
  const agentY = centerY - AGENT_H / 2;
  let curX = agentX + AGENT_W + H_GAP;

  const llmCards: NewLLMCard[] = [];
  const toolGroups: Array<{ nodes: NewToolNode[]; stepIndex: number; parallelCount: number }> = [];

  toolSequence.forEach((step, si) => {
    llmCards.push({ step, x: curX, y: centerY - LLM_H / 2, stepNum: si + 1 });
    curX += LLM_W + H_GAP;

    if (step.toolCalls.length > 0) {
      const count = step.toolCalls.length;
      const totalH = count * TOOL_H + Math.max(0, count - 1) * V_GAP;
      const startY = centerY - totalH / 2;
      const nodes: NewToolNode[] = step.toolCalls.map((tc, j) => {
        const content = tc.id != null ? (globalResultMap.get(tc.id) ?? null) : null;
        return {
          id: `new-tool-${si}-${j}`,
          name: tc.name ?? "?",
          x: curX,
          y: startY + j * (TOOL_H + V_GAP),
          hasResult: content != null,
          resultContent: content,
          isResultError: content != null && isToolResultError(content),
          stepIndex: si,
        };
      });
      toolGroups.push({ nodes, stepIndex: si, parallelCount: count });
      curX += TOOL_W + H_GAP;
    }
  });

  const svgW = curX - H_GAP + SEQ_PAD;

  // Build edges
  const edges: NewEdge[] = [];

  if (llmCards.length > 0) {
    edges.push({
      x1: agentX + AGENT_W - 2, y1: centerY,
      x2: llmCards[0].x + 10,   y2: llmCards[0].y + LLM_H / 2,
      color: "#22c55e", dashed: false,
    });
  }

  for (let i = 0; i < llmCards.length; i++) {
    const card = llmCards[i];
    const group = toolGroups.find(g => g.stepIndex === i);

    if (group) {
      // LLM card → tool nodes (outgoing calls, orange)
      for (const tn of group.nodes) {
        edges.push({
          x1: card.x + LLM_W - 2, y1: card.y + LLM_H / 2,
          x2: tn.x + 10,           y2: tn.y + TOOL_H / 2,
          color: "#f97316", dashed: false,
        });
      }
      // Tool nodes → next LLM card (returning results, green)
      if (i + 1 < llmCards.length) {
        const nextCard = llmCards[i + 1];
        for (const tn of group.nodes) {
          edges.push({
            x1: tn.x + TOOL_W - 2, y1: tn.y + TOOL_H / 2,
            x2: nextCard.x + 10,   y2: nextCard.y + LLM_H / 2,
            color: "#22c55e", dashed: false,
          });
        }
      }
    } else if (i + 1 < llmCards.length) {
      // Consecutive LLM cards with no tools between them
      const nextCard = llmCards[i + 1];
      edges.push({
        x1: card.x + LLM_W - 2, y1: card.y + LLM_H / 2,
        x2: nextCard.x + 10,    y2: nextCard.y + LLM_H / 2,
        color: "#4b5563", dashed: true,
      });
    }
  }

  return { llmCards, toolGroups, agentX, agentY, edges, svgW, svgH, centerY };
}

function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const cx1 = x1 + (x2 - x1) * 0.45;
  const cx2 = x1 + (x2 - x1) * 0.55;
  return `M ${x1} ${y1} C ${cx1} ${y1} ${cx2} ${y2} ${x2} ${y2}`;
}

// ─── Detail panel ─────────────────────────────────────────────────────────────
function LLMDetailPanel({ step, stepNum, onClose }: { step: ToolCallStep; stepNum: number; onClose: () => void }) {
  const isError = !!(step.error || (step.statusCode != null && step.statusCode >= 400));

  return (
    <div className="mt-4 p-4 bg-gray-900/80 border border-gray-700 rounded-lg space-y-3">
      <div className="flex justify-between items-start">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-gray-200">Call #{stepNum}</span>
          <span className="text-xs text-gray-500 font-mono">{new Date(step.timestamp).toLocaleTimeString()}</span>
          <span className={`text-xs font-semibold ${isError ? "text-red-400" : "text-green-400"}`}>
            {step.statusCode ?? "?"} {isError ? "error" : "ok"}
          </span>
        </div>
        <button onClick={onClose} className="text-gray-600 hover:text-gray-300 text-xl leading-none">×</button>
      </div>

      {isError ? (
        /* Error state: show only the error message */
        <div>
          <div className="text-xs text-gray-500 mb-1">Error</div>
          <div className="text-xs text-red-400 font-mono bg-gray-950 rounded p-2 border border-red-900/30 whitespace-pre-wrap break-words">
            {step.error ?? `HTTP ${step.statusCode}`}
          </div>
        </div>
      ) : (
        /* Success state: show full panel */
        <>
          {/* Metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <div>
              <div className="text-gray-500 mb-0.5">Model</div>
              <div className="text-blue-300 font-mono truncate">{step.model ?? step.provider}</div>
            </div>
            <div>
              <div className="text-gray-500 mb-0.5">Latency</div>
              <div className="text-gray-200">{fmt(step.latencyMs)}</div>
            </div>
            <div>
              <div className="text-gray-500 mb-0.5">Tokens in</div>
              <div className="text-gray-200">{step.inputTokens?.toLocaleString() ?? "—"}</div>
            </div>
            <div>
              <div className="text-gray-500 mb-0.5">Tokens out</div>
              <div className="text-gray-200">{step.outputTokens?.toLocaleString() ?? "—"}</div>
            </div>
          </div>

          {/* System prompt */}
          {step.systemPromptPreview && (
            <div>
              <div className="text-xs text-gray-500 mb-1">System prompt (preview)</div>
              <pre className="text-xs font-mono text-purple-300 bg-gray-950 rounded p-2 max-h-28 overflow-y-auto whitespace-pre-wrap border border-gray-800">
                {step.systemPromptPreview}
              </pre>
            </div>
          )}

          {/* Tool results received by this call */}
          {step.toolResults.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1">Tool results received ({step.toolResults.length})</div>
              <div className="space-y-1 max-h-24 overflow-y-auto">
                {step.toolResults.map((tr, i) => (
                  <div key={i} className="text-xs font-mono bg-green-900/20 border border-green-800/30 rounded px-2 py-1 text-green-300 truncate">
                    {tr.toolCallId ? `[${tr.toolCallId.slice(0, 8)}…] ` : ""}
                    {tr.content.slice(0, 100)}{tr.content.length > 100 ? "…" : ""}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Response text */}
          {step.responseText && (
            <div>
              <div className="text-xs text-gray-500 mb-1">Response</div>
              <pre className="text-xs font-mono text-teal-300 bg-gray-950 rounded p-2 max-h-32 overflow-y-auto whitespace-pre-wrap border border-gray-800">
                {step.responseText}
              </pre>
            </div>
          )}

          {/* Tool calls made */}
          {step.toolCalls.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1">Tool calls made ({step.toolCalls.length})</div>
              <div className="space-y-1">
                {step.toolCalls.map((tc, i) => (
                  <div key={i} className="text-xs font-mono bg-orange-900/20 border border-orange-800/30 rounded px-2 py-1 text-orange-300">
                    <span className="font-semibold">{tc.name ?? "?"}</span>
                    {Object.keys(tc.input).length > 0 && (
                      <span className="text-gray-500 ml-2">
                        ({Object.entries(tc.input).slice(0, 2).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Tooltip ──────────────────────────────────────────────────────────────────
interface TooltipData { x: number; y: number; content: React.ReactNode }

// ─── Props ────────────────────────────────────────────────────────────────────
interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  toolSequence?: ToolCallStep[];
  onNodeClick?: (nodeId: string) => void;
  onEdgeClick?: (edge: GraphEdge) => void;
}

// ─── Component ───────────────────────────────────────────────────────────────
export default function GraphView({ nodes, edges, toolSequence, onNodeClick, onEdgeClick }: Props) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const [selectedStep, setSelectedStep] = useState<ToolCallStep | null>(null);

  const useSeq = (toolSequence?.length ?? 0) > 0;

  // ── Aggregate view setup ──────────────────────────────────────────────────
  const aggLayout = useSeq ? [] : computeLayout(nodes);
  const posMap = new Map<string, { cx: number; cy: number }>();
  for (const l of aggLayout) {
    posMap.set(l.node.id, { cx: l.x + NODE_W / 2, cy: l.y + NODE_H / 2 });
  }

  const svgW_agg = aggLayout.length > 0
    ? Math.max(...aggLayout.map(l => l.x + NODE_W)) + PADDING * 2
    : 400;
  const svgH_agg = aggLayout.length > 0
    ? Math.max(...aggLayout.map(l => l.y + NODE_H)) + PADDING * 2
    : 200;

  // ── Sequential view setup ─────────────────────────────────────────────────
  const seqLayout = useSeq ? computeNewSeqLayout(toolSequence!) : null;
  const svgW = useSeq ? seqLayout!.svgW : svgW_agg;
  const svgH = useSeq ? seqLayout!.svgH : svgH_agg;

  // ── Aggregate view handlers ───────────────────────────────────────────────
  const handleNodeEnter = useCallback((_e: React.MouseEvent<SVGGElement>, node: GraphNode) => {
    const pos = posMap.get(node.id);
    if (!pos) return;
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
          <div>p95: <span className="text-gray-200">{fmt(node.metrics.p95LatencyMs)}</span></div>
          <div>Tokens: <span className="text-gray-200">{node.metrics.totalTokens.toLocaleString()}</span></div>
          <div>Cost: <span className="text-gray-200">${node.metrics.totalCostUsd.toFixed(4)}</span></div>
          <div>Error rate: <span className={node.metrics.errorRate > 0.05 ? "text-red-400" : "text-gray-200"}>
            {(node.metrics.errorRate * 100).toFixed(1)}%
          </span></div>
        </div>
      ),
    });
  }, [posMap]);

  const handleEdgeEnter = useCallback((_e: React.MouseEvent, fromId: string, toId: string, edgeData: {
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
          <div>Tokens: <span className="text-gray-200">{edgeData.totalTokens.toLocaleString()}</span></div>
          <div>Cost: <span className="text-gray-200">${edgeData.totalCostUsd.toFixed(4)}</span></div>
          <div>Error rate: <span className={edgeData.errorRate > 0.05 ? "text-red-400" : "text-gray-200"}>
            {(edgeData.errorRate * 100).toFixed(1)}%
          </span></div>
        </div>
      ),
    });
  }, [posMap]);

  const renderAggNode = (node: GraphNode, x: number, y: number) => {
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
        <rect x={x} y={y} width={NODE_W} height={NODE_H} rx={8} fill={colors.fill} stroke={colors.stroke} strokeWidth={1.5} />
        <rect x={x + 4} y={y + NODE_H - 5} width={NODE_W - 8} height={3} rx={1.5} fill={bgColor} opacity={0.6} />
        <text x={x + NODE_W / 2} y={y + 16} textAnchor="middle" fontSize={11} fontWeight="600" fill={colors.text}>
          {node.label.length > 14 ? node.label.slice(0, 13) + "…" : node.label}
        </text>
        <text x={x + NODE_W / 2} y={y + 30} textAnchor="middle" fontSize={9} fill="#6b7280">
          {node.metrics.callCount} calls · {fmt(node.metrics.avgLatencyMs)}
        </text>
      </g>
    );
  };

  // ── Sequential view renderers ─────────────────────────────────────────────
  const handleLLMCardClick = useCallback((step: ToolCallStep) => {
    setSelectedStep(prev => prev?.interactionId === step.interactionId ? null : step);
    onNodeClick?.(step.interactionId);
  }, [onNodeClick]);

  const renderAgentNode = (x: number, y: number) => (
    <g key="agent-seq" className="cursor-default">
      <rect x={x} y={y} width={AGENT_W} height={AGENT_H} rx={8}
        fill={TYPE_COLORS.agent.fill} stroke={TYPE_COLORS.agent.stroke} strokeWidth={1.5} />
      <text x={x + AGENT_W / 2} y={y + 17} textAnchor="middle" fontSize={11} fontWeight="600" fill={TYPE_COLORS.agent.text}>
        Agent
      </text>
      <text x={x + AGENT_W / 2} y={y + 31} textAnchor="middle" fontSize={9} fill="#6b7280">
        start
      </text>
    </g>
  );

  const renderLLMCard = (step: ToolCallStep, x: number, y: number, stepNum: number, isSelected: boolean, onClick: () => void) => {
    const isError = !!(step.error || (step.statusCode != null && step.statusCode >= 400));
    const statusColor = isError ? "#ef4444" : "#22c55e";
    const modelLabel = step.model
      ? step.model.length > 19 ? step.model.slice(0, 18) + "…" : step.model
      : step.provider;
    const respPreview = step.responseText
      ? step.responseText.slice(0, 20) + (step.responseText.length > 20 ? "…" : "")
      : isError
      ? (step.error?.slice(0, 20) ?? "Error")
      : "—";

    const callStroke = isSelected ? "#60a5fa" : "#1d4ed8";
    const respStroke = isSelected ? "#2dd4bf" : "#0f766e";
    const RESP_H = LLM_H - LLM_CALL_H;

    return (
      <g key={step.interactionId} className="cursor-pointer" onClick={onClick}>
        {/* Call section */}
        <rect x={x} y={y} width={LLM_W} height={LLM_CALL_H} rx={8} fill="#1e3a5f" stroke={callStroke} strokeWidth={isSelected ? 2 : 1.5} />
        {/* Flat bottom of call section */}
        <rect x={x} y={y + LLM_CALL_H - 8} width={LLM_W} height={8} fill="#1e3a5f" />

        <circle cx={x + LLM_W - 10} cy={y + 10} r={4} fill={statusColor} />
        <text x={x + 8} y={y + 12} fontSize={8} fill="#4b5563">Call #{stepNum} · via interceptor</text>
        <text x={x + LLM_W / 2} y={y + 27} textAnchor="middle" fontSize={11} fontWeight="600" fill="#93c5fd">
          {modelLabel}
        </text>
        {step.inputTokens != null && (
          <text x={x + LLM_W / 2} y={y + 39} textAnchor="middle" fontSize={9} fill="#4b5563">
            {step.inputTokens.toLocaleString()} tokens in
          </text>
        )}

        {/* Divider */}
        <line x1={x} y1={y + LLM_CALL_H} x2={x + LLM_W} y2={y + LLM_CALL_H} stroke="#374151" strokeWidth={1} />

        {/* Response section */}
        <rect x={x} y={y + LLM_CALL_H} width={LLM_W} height={RESP_H} rx={8} fill="#1e3030" stroke={respStroke} strokeWidth={isSelected ? 2 : 1.5} />
        {/* Flat top of response section */}
        <rect x={x} y={y + LLM_CALL_H} width={LLM_W} height={8} fill="#1e3030" />

        <text x={x + 8} y={y + LLM_CALL_H + 14} fontSize={9} fill="#6b7280">
          {fmt(step.latencyMs)}{step.outputTokens != null ? `  ·  ${step.outputTokens} out` : ""}
        </text>
        <text x={x + LLM_W / 2} y={y + LLM_CALL_H + 28} textAnchor="middle" fontSize={9} fill="#5eead4" fontStyle="italic">
          {respPreview}
        </text>
        {step.toolCalls.length > 0 && (
          <text x={x + LLM_W / 2} y={y + LLM_CALL_H + 41} textAnchor="middle" fontSize={9} fill="#f97316">
            → {step.toolCalls.length} tool call{step.toolCalls.length > 1 ? "s" : ""}
          </text>
        )}

        {/* Selected highlight ring */}
        {isSelected && (
          <rect x={x - 2} y={y - 2} width={LLM_W + 4} height={LLM_H + 4} rx={10}
            fill="none" stroke="#60a5fa" strokeWidth={2} strokeDasharray="4 2" opacity={0.6} />
        )}
      </g>
    );
  };

  const renderNewToolNode = (id: string, name: string, x: number, y: number, hasResult: boolean, resultContent: string | null, isResultError: boolean) => {
    const stroke = !hasResult ? "#f97316" : isResultError ? "#ef4444" : "#22c55e";
    const nameColor = !hasResult ? "#fdba74" : isResultError ? "#fca5a5" : "#86efac";
    const statusColor = !hasResult ? "#f97316" : isResultError ? "#ef4444" : "#22c55e";
    const resultColor = isResultError ? "#f87171" : "#6ee7b7";
    const DIVIDER_Y = y + 36;

    // Truncate result to two short lines
    const preview = resultContent
      ? resultContent.replace(/\s+/g, " ").trim().slice(0, 52)
      : null;
    const line1 = preview ? preview.slice(0, 26) : null;
    const line2 = preview && preview.length > 26 ? preview.slice(26) + (resultContent!.length > 52 ? "…" : "") : null;

    return (
      <g key={id}>
        <rect x={x} y={y} width={TOOL_W} height={TOOL_H} rx={8} fill="#3b1f00" stroke={stroke} strokeWidth={1.5} />

        {/* Status dot */}
        <circle cx={x + TOOL_W - 10} cy={y + 10} r={4} fill={statusColor} />

        {/* Tool name */}
        <text x={x + TOOL_W / 2} y={y + 14} textAnchor="middle" fontSize={10} fontWeight="600" fill={nameColor}>
          {name.length > 16 ? name.slice(0, 15) + "…" : name}
        </text>

        {/* Status label */}
        <text x={x + TOOL_W / 2} y={y + 27} textAnchor="middle" fontSize={9} fill={statusColor}>
          {!hasResult ? "⋯ pending" : isResultError ? "⚠ error result" : "✓ executed"}
        </text>

        {/* Divider + result preview */}
        {resultContent && (
          <>
            <line x1={x + 6} y1={DIVIDER_Y} x2={x + TOOL_W - 6} y2={DIVIDER_Y}
              stroke={stroke} strokeWidth={0.5} strokeOpacity={0.4} />
            {line1 && (
              <text x={x + TOOL_W / 2} y={DIVIDER_Y + 12} textAnchor="middle" fontSize={8} fill={resultColor} fontStyle="italic">
                {line1}
              </text>
            )}
            {line2 && (
              <text x={x + TOOL_W / 2} y={DIVIDER_Y + 23} textAnchor="middle" fontSize={8} fill={resultColor} fontStyle="italic">
                {line2}
              </text>
            )}
          </>
        )}
      </g>
    );
  };

  // ── Render ────────────────────────────────────────────────────────────────
  const allMarkerColors = ["#22c55e", "#f59e0b", "#ef4444", "#4b5563", "#f97316"];

  return (
    <div className="relative overflow-x-auto">
      <svg
        width={svgW}
        height={svgH}
        className="rounded bg-gray-900/50 border border-gray-800 min-w-full"
        onMouseLeave={() => setTooltip(null)}
      >
        <defs>
          {allMarkerColors.map(color => (
            <marker key={color} id={`arrow-${color.replace("#", "")}`}
              markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill={color} />
            </marker>
          ))}
        </defs>

        {useSeq && seqLayout ? (
          <>
            {/* Edges */}
            {seqLayout.edges.map((edge, i) => {
              const markerId = `arrow-${edge.color.replace("#", "")}`;
              return (
                <path key={i}
                  d={edgePath(edge.x1, edge.y1, edge.x2, edge.y2)}
                  stroke={edge.color}
                  strokeWidth={1.5}
                  strokeDasharray={edge.dashed ? "4 3" : undefined}
                  fill="none"
                  opacity={0.7}
                  markerEnd={`url(#${markerId})`}
                />
              );
            })}

            {/* Parallel grouping rects */}
            {seqLayout.toolGroups
              .filter(g => g.parallelCount > 1)
              .map(g => {
                const ns = g.nodes;
                const rx = ns[0].x - 8;
                const ry = ns[0].y - 8;
                const rw = TOOL_W + 16;
                const rh = ns[ns.length - 1].y + TOOL_H - ns[0].y + 16;
                return (
                  <g key={`prect-${g.stepIndex}`}>
                    <rect x={rx} y={ry} width={rw} height={rh} rx={10}
                      fill="#3b1f00" fillOpacity={0.4} stroke="#f97316" strokeOpacity={0.3} strokeWidth={1} />
                    <text x={rx + rw / 2} y={ry - 4} textAnchor="middle" fontSize={9} fill="#f97316" fillOpacity={0.7}>
                      parallel
                    </text>
                  </g>
                );
              })}

            {/* Agent node */}
            {renderAgentNode(seqLayout.agentX, seqLayout.agentY)}

            {/* LLM cards */}
            {seqLayout.llmCards.map(({ step, x, y, stepNum }) =>
              renderLLMCard(step, x, y, stepNum,
                selectedStep?.interactionId === step.interactionId,
                () => handleLLMCardClick(step)
              )
            )}

            {/* Tool nodes */}
            {seqLayout.toolGroups.flatMap(g =>
              g.nodes.map(tn => renderNewToolNode(tn.id, tn.name, tn.x, tn.y, tn.hasResult, tn.resultContent, tn.isResultError))
            )}
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
                <path key={i}
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
            {aggLayout.map(({ node, x, y }) => renderAggNode(node, x, y))}
          </>
        )}
      </svg>

      {/* Tooltip (aggregate view only) */}
      {!useSeq && tooltip && (
        <div
          className="absolute z-20 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 shadow-xl pointer-events-none"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          {tooltip.content}
        </div>
      )}

      {/* Detail panel (sequential view) */}
      {useSeq && selectedStep && seqLayout && (
        <LLMDetailPanel
          step={selectedStep}
          stepNum={seqLayout.llmCards.findIndex(c => c.step.interactionId === selectedStep.interactionId) + 1}
          onClose={() => { setSelectedStep(null); onNodeClick?.(""); }}
        />
      )}

      {/* Legend */}
      <div className="flex gap-4 mt-2 text-xs text-gray-500 flex-wrap">
        {useSeq ? (
          <>
            <span className="flex items-center gap-1">
              <span className="inline-block w-6 h-1 rounded" style={{ background: "#22c55e" }} /> Result returned
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-6 h-1 rounded" style={{ background: "#f97316" }} /> Tool call
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded-full border" style={{ background: "#22c55e" }} /> Tool ok
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded-full border" style={{ background: "#ef4444" }} /> Tool error
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded-full border" style={{ background: "#f97316" }} /> Pending
            </span>
            <span className="text-gray-600">Click an LLM card to inspect it</span>
          </>
        ) : (
          <>
            <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#22c55e" }} /> Low error</span>
            <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#f59e0b" }} /> Some errors</span>
            <span className="flex items-center gap-1"><span className="inline-block w-6 h-1 rounded" style={{ background: "#ef4444" }} /> High error</span>
            <span className="flex items-center gap-1 ml-2"><span className="inline-block w-3 h-3 rounded border border-gray-500" style={{ background: "#166534" }} /> Fast</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded border border-gray-500" style={{ background: "#854d0e" }} /> Slow</span>
          </>
        )}
      </div>
    </div>
  );
}
