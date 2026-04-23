import { useMemo, useEffect, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Edge,
  type Node,
  useNodesState,
  useEdgesState,
  MarkerType,
} from "reactflow";
import dagre from "dagre";
import "reactflow/dist/style.css";

import AgentNodeComp, { type AgentNodeData } from "./nodes/AgentNode";
import ToolNodeComp, { type ToolNodeData } from "./nodes/ToolNode";
import type { ConversationData } from "../../hooks/useConversationData";
import type { PlayheadApi } from "../../hooks/usePlayhead";

interface Props {
  data: ConversationData;
  playhead: PlayheadApi;
}

const AGENT_W = 200;
const AGENT_H = 76;
const TOOL_W = 140;
const TOOL_H = 52;
const RETURN_EDGE_DELAY_MS = 300;

const nodeTypes = { agent: AgentNodeComp, tool: ToolNodeComp };

function layoutDagre(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => {
    const isTool = n.type === "tool";
    g.setNode(n.id, {
      width: isTool ? TOOL_W : AGENT_W,
      height: isTool ? TOOL_H : AGENT_H,
    });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const pos = g.node(n.id);
    const isTool = n.type === "tool";
    const w = isTool ? TOOL_W : AGENT_W;
    const h = isTool ? TOOL_H : AGENT_H;
    return { ...n, position: { x: pos.x - w / 2, y: pos.y - h / 2 } };
  });
}

function uniqueToolNames(toolCalls: Record<string, unknown>[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const tc of toolCalls) {
    const name = typeof tc.name === "string" && tc.name ? tc.name : "tool";
    if (!seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

function roleVar(role: string): string {
  switch (role) {
    case "orchestrator": return "--role-orchestrator";
    case "subagent":     return "--role-subagent";
    case "tool":         return "--role-tool";
    default:             return "--role-unknown";
  }
}

export default function AgentFlowGraph({ data, playhead }: Props) {
  const currentTurn = data.turns[playhead.idx] ?? null;
  const currentTurnId = currentTurn?.id ?? null;

  // Staged edges: show the agent→tool arrow immediately, then the tool→agent
  // return arrow after a short delay so the round-trip reads as a sequence.
  const [returnPhase, setReturnPhase] = useState<"out" | "both">("out");
  useEffect(() => {
    setReturnPhase("out");
    if (!currentTurnId) return;
    const id = window.setTimeout(() => setReturnPhase("both"), RETURN_EDGE_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [currentTurnId]);

  // Build raw nodes/edges from conversation data.
  const { rawNodes, rawEdges } = useMemo(() => {
    const pastSessions = new Set<string>();
    for (let i = 0; i <= playhead.idx && i < data.turns.length; i++) {
      pastSessions.add(data.turns[i].sessionId);
    }

    const nodes: Node<AgentNodeData | ToolNodeData>[] = (data.graph?.nodes ?? []).map((n) => ({
      id: n.session_id,
      type: "agent",
      position: { x: 0, y: 0 },
      data: {
        sessionId: n.session_id,
        role: n.agent_role ?? "unknown",
        label: n.session_id.slice(0, 10) + (n.session_id.length > 10 ? "…" : ""),
        callCount: n.interaction_count,
        tokens: n.total_tokens,
        costUsd: n.total_cost_usd,
        active: currentTurn?.sessionId === n.session_id,
        past: pastSessions.has(n.session_id),
      },
    }));

    // Aggregate directed handoff edges (from_session → to_session).
    const edgeMap = new Map<string, { source: string; target: string; count: number; firstTurn: number; latestTurn: number }>();
    for (const e of data.graph?.edges ?? []) {
      const key = `${e.from_session_id}__${e.to_session_id}`;
      const existing = edgeMap.get(key);
      if (existing) {
        existing.count += 1;
        existing.latestTurn = Math.max(existing.latestTurn, e.turn_number);
      } else {
        edgeMap.set(key, {
          source: e.from_session_id,
          target: e.to_session_id,
          count: 1,
          firstTurn: e.turn_number,
          latestTurn: e.turn_number,
        });
      }
    }

    const edges: Edge[] = [];
    for (const [key, e] of edgeMap) {
      const traversed = currentTurn ? e.firstTurn <= currentTurn.turnNumber : false;
      const active = currentTurn ? e.firstTurn <= currentTurn.turnNumber && e.latestTurn >= currentTurn.turnNumber : false;
      const targetRoleVar = roleVar(data.roleBySession.get(e.target) ?? "unknown");
      const color = `rgb(var(${targetRoleVar})${traversed ? "" : " / 0.35"})`;
      edges.push({
        id: key,
        source: e.source,
        target: e.target,
        animated: active,
        style: {
          stroke: color,
          strokeWidth: active ? 2.5 : traversed ? 1.75 : 1.25,
        },
        label: e.count > 1 ? `×${e.count}` : undefined,
        labelStyle: {
          fill: "rgb(var(--fg-secondary))",
          fontSize: 10,
        },
        labelBgStyle: {
          fill: "rgb(var(--bg-surface))",
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color,
          width: 16,
          height: 16,
        },
      });
    }

    // Transient tool nodes + staged round-trip edges for the current turn.
    if (currentTurn && currentTurn.toolCalls.length > 0) {
      const stroke = "rgb(var(--role-tool))";
      const names = uniqueToolNames(currentTurn.toolCalls);
      for (const name of names) {
        const toolId = `tool::${currentTurn.sessionId}::${currentTurn.id}::${name}`;
        nodes.push({
          id: toolId,
          type: "tool",
          position: { x: 0, y: 0 },
          data: { name, turnId: currentTurn.id },
        });
        edges.push({
          id: `${toolId}::out`,
          source: currentTurn.sessionId,
          target: toolId,
          animated: false,
          style: { stroke, strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 14, height: 14 },
        });
        if (returnPhase === "both") {
          edges.push({
            id: `${toolId}::back`,
            source: toolId,
            target: currentTurn.sessionId,
            animated: true,
            style: { stroke, strokeWidth: 1.75 },
            markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 14, height: 14 },
          });
        }
      }
    }

    return { rawNodes: nodes, rawEdges: edges };
  }, [data, playhead.idx, currentTurn, returnPhase]);

  const layoutedNodes = useMemo(() => layoutDagre(rawNodes, rawEdges), [rawNodes, rawEdges]);
  const [nodes, setNodes, onNodesChange] = useNodesState(layoutedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(rawEdges);

  // Sync computed nodes/edges back into RF state when data or playhead changes.
  useEffect(() => { setNodes(layoutedNodes); }, [layoutedNodes, setNodes]);
  useEffect(() => { setEdges(rawEdges); }, [rawEdges, setEdges]);

  const handleNodeClick = (_: unknown, node: Node) => {
    // Jump playhead to the first turn in this session at or after current idx.
    const idx = data.turns.findIndex((t) => t.sessionId === node.id);
    if (idx >= 0) playhead.setIdx(idx);
  };

  if ((data.graph?.nodes ?? []).length === 0) {
    return (
      <div className="h-full w-full flex items-center justify-center text-fg-muted text-sm">
        No agents detected in this conversation.
      </div>
    );
  }

  return (
    <div className="h-full w-full bg-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="rgb(var(--border-soft))" gap={16} />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(n) => {
            const role = (n.data as AgentNodeData | undefined)?.role ?? "unknown";
            return `rgb(var(${roleVar(role)}))`;
          }}
          maskColor="rgb(var(--bg-canvas) / 0.7)"
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
}
