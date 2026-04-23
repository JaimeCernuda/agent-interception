import { useMemo, useRef, useState, useEffect, useCallback } from "react";
import { scaleLinear } from "@visx/scale";
import { AxisBottom } from "@visx/axis";
import { Group } from "@visx/group";
import type { ConversationData, NormalizedTurn } from "../../hooks/useConversationData";
import type { PlayheadApi } from "../../hooks/usePlayhead";
import { PLAYHEAD_SPEEDS } from "../../hooks/usePlayhead";

interface Props {
  data: ConversationData;
  playhead: PlayheadApi;
}

const LANE_HEIGHT = 28;
const LANE_LABEL_WIDTH = 160;
const TOP_PADDING = 28;       // leaves room for the axis
const BOTTOM_PADDING = 32;
const MIN_BAR_WIDTH = 3;
const LABEL_MAX_CHARS = 18;

interface TooltipState {
  turn: NormalizedTurn;
  x: number;
  y: number;
}

function roleColorVar(role: string): string {
  switch (role) {
    case "orchestrator": return "var(--role-orchestrator)";
    case "subagent":     return "var(--role-subagent)";
    case "tool":         return "var(--role-tool)";
    default:             return "var(--role-unknown)";
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function formatCost(usd: number | null): string {
  if (usd == null) return "—";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
}

export default function TimelineView({ data, playhead }: Props) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const chartScrollRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  useEffect(() => {
    if (!wrapperRef.current) return;
    const el = wrapperRef.current;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    obs.observe(el);
    setWidth(el.clientWidth);
    return () => obs.disconnect();
  }, []);

  const { turns, lanes, roleBySession } = data;

  const { t0, duration } = useMemo(() => {
    if (turns.length === 0) return { t0: 0, duration: 1 };
    const start = turns[0].startTs;
    const last = turns[turns.length - 1];
    const end = last.startTs + (last.hasLatency ? last.latencyMs : 0);
    return { t0: start, duration: Math.max(end - start, 1) };
  }, [turns]);

  const callsByLane = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of turns) m.set(t.sessionId, (m.get(t.sessionId) ?? 0) + 1);
    return m;
  }, [turns]);

  const chartWidth = Math.max(width - LANE_LABEL_WIDTH - 16, 200);
  const chartHeight = lanes.length * LANE_HEIGHT;
  const svgHeight = TOP_PADDING + chartHeight + BOTTOM_PADDING;

  const xScale = useMemo(
    () => scaleLinear({
      domain: [0, duration],
      range: [0, chartWidth],
    }),
    [duration, chartWidth],
  );

  const laneY = useCallback((sessionId: string) => {
    const i = lanes.indexOf(sessionId);
    return TOP_PADDING + (i < 0 ? 0 : i) * LANE_HEIGHT + (LANE_HEIGHT - 18) / 2;
  }, [lanes]);

  const currentTurn = turns[playhead.idx] ?? null;
  const playheadX = currentTurn ? xScale(currentTurn.startTs - t0) : 0;

  const onScrubberDrag = useCallback((clientX: number, rect: DOMRect) => {
    if (turns.length === 0) return;
    const x = Math.max(0, Math.min(chartWidth, clientX - rect.left));
    const t = xScale.invert(x);
    // Find closest turn by startTs.
    let nearestIdx = 0;
    let nearestDist = Infinity;
    for (let i = 0; i < turns.length; i++) {
      const d = Math.abs((turns[i].startTs - t0) - t);
      if (d < nearestDist) { nearestDist = d; nearestIdx = i; }
    }
    playhead.setIdx(nearestIdx);
  }, [turns, xScale, t0, chartWidth, playhead]);

  const scrubberDragging = useRef(false);

  const handleBarEnter = useCallback(
    (turn: NormalizedTurn, evt: React.MouseEvent) => {
      const container = chartScrollRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      setTooltip({
        turn,
        x: evt.clientX - rect.left + container.scrollLeft,
        y: evt.clientY - rect.top + container.scrollTop,
      });
    },
    [],
  );

  const handleBarMove = useCallback(
    (evt: React.MouseEvent) => {
      setTooltip((prev) => {
        if (!prev) return prev;
        const container = chartScrollRef.current;
        if (!container) return prev;
        const rect = container.getBoundingClientRect();
        return {
          turn: prev.turn,
          x: evt.clientX - rect.left + container.scrollLeft,
          y: evt.clientY - rect.top + container.scrollTop,
        };
      });
    },
    [],
  );

  const handleBarLeave = useCallback(() => setTooltip(null), []);

  return (
    <div ref={wrapperRef} className="h-full w-full bg-canvas border-t border-border-soft flex flex-col">
      {/* Controls */}
      <div className="px-3 py-1.5 border-b border-border-soft flex items-center gap-3 shrink-0">
        <button
          onClick={playhead.toggle}
          className="w-7 h-7 rounded-md bg-elevate hover:bg-hover text-fg-primary flex items-center justify-center"
          title={playhead.isPlaying ? "Pause (space)" : "Play (space)"}
        >
          {playhead.isPlaying ? (
            <svg width="12" height="12" viewBox="0 0 12 12"><rect x="3" y="2" width="2" height="8" fill="currentColor"/><rect x="7" y="2" width="2" height="8" fill="currentColor"/></svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 12 12"><polygon points="3,2 10,6 3,10" fill="currentColor"/></svg>
          )}
        </button>
        <button
          onClick={() => playhead.step(-1)}
          className="w-7 h-7 rounded-md bg-elevate hover:bg-hover text-fg-secondary flex items-center justify-center"
          title="Previous turn (←)"
        ><svg width="10" height="10" viewBox="0 0 10 10"><polygon points="7,1 3,5 7,9" fill="currentColor"/></svg></button>
        <button
          onClick={() => playhead.step(1)}
          className="w-7 h-7 rounded-md bg-elevate hover:bg-hover text-fg-secondary flex items-center justify-center"
          title="Next turn (→)"
        ><svg width="10" height="10" viewBox="0 0 10 10"><polygon points="3,1 7,5 3,9" fill="currentColor"/></svg></button>

        <span className="text-xs text-fg-muted tabular-nums">
          {playhead.idx + 1} / {playhead.count}
        </span>

        <div className="flex items-center gap-1 ml-2">
          {PLAYHEAD_SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => playhead.setSpeed(s)}
              className={`px-2 py-0.5 text-[10px] rounded ${
                playhead.speed === s ? "bg-accent text-canvas" : "bg-elevate text-fg-muted hover:text-fg-secondary"
              }`}
            >{s}x</button>
          ))}
        </div>

        <div className="flex-1" />

        {/* Legend: one dot per distinct role + error swatch */}
        <div className="flex items-center gap-3 text-[10px] text-fg-muted">
          {Array.from(new Set(Array.from(roleBySession.values()))).map((role) => (
            <span key={role} className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ background: `rgb(${roleColorVar(role)})` }} />
              {role}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-[color:var(--error)]" />
            error
          </span>
        </div>
      </div>

      {/* Chart */}
      <div ref={chartScrollRef} className="flex-1 overflow-auto relative">
        <svg width={width} height={svgHeight} className="block">
          {/* Lane backgrounds + labels — clickable, jump to first turn in lane */}
          {lanes.map((sid, i) => {
            const y = TOP_PADDING + i * LANE_HEIGHT;
            const role = roleBySession.get(sid) ?? "unknown";
            const isCurrent = currentTurn?.sessionId === sid;
            const calls = callsByLane.get(sid) ?? 0;
            const onClickLane = () => {
              const idx = turns.findIndex((t) => t.sessionId === sid);
              if (idx >= 0) playhead.setIdx(idx);
            };
            return (
              <Group key={sid}>
                <rect
                  x={0}
                  y={y}
                  width={width}
                  height={LANE_HEIGHT}
                  fill={isCurrent ? "rgb(var(--bg-elevate) / 0.5)" : i % 2 === 0 ? "rgb(var(--bg-surface) / 0.3)" : "transparent"}
                />
                <line x1={LANE_LABEL_WIDTH} y1={y + LANE_HEIGHT} x2={width} y2={y + LANE_HEIGHT}
                  stroke="rgb(var(--border-soft))" strokeWidth="1" />
                {/* Clickable label group */}
                <g onClick={onClickLane} style={{ cursor: "pointer" }}>
                  <rect
                    x={0} y={y} width={LANE_LABEL_WIDTH} height={LANE_HEIGHT}
                    fill="transparent"
                  />
                  <circle cx={10} cy={y + LANE_HEIGHT / 2} r={4} fill={`rgb(${roleColorVar(role)})`} />
                  <text
                    x={22} y={y + LANE_HEIGHT / 2 + 4}
                    fontSize="11"
                    fill="rgb(var(--fg-secondary))"
                    style={{ fontFamily: "ui-monospace, monospace" }}
                  >
                    {truncate(sid, LABEL_MAX_CHARS)}
                  </text>
                  <text
                    x={LANE_LABEL_WIDTH - 6} y={y + LANE_HEIGHT / 2 + 4}
                    fontSize="10"
                    textAnchor="end"
                    fill="rgb(var(--fg-muted))"
                    style={{ fontFeatureSettings: "'tnum'" }}
                  >
                    {calls}
                  </text>
                </g>
              </Group>
            );
          })}

          {/* Scrub overlay — FIRST (under bars) so empty area scrubs but bars
              receive click/hover themselves. */}
          <rect
            x={LANE_LABEL_WIDTH} y={TOP_PADDING - 12}
            width={chartWidth} height={chartHeight + 14}
            fill="transparent"
            style={{ cursor: "ew-resize" }}
            onPointerDown={(e) => {
              scrubberDragging.current = true;
              (e.target as Element).setPointerCapture?.(e.pointerId);
              onScrubberDrag(e.clientX, (e.currentTarget as SVGRectElement).ownerSVGElement!.getBoundingClientRect());
            }}
            onPointerMove={(e) => {
              if (!scrubberDragging.current) return;
              onScrubberDrag(e.clientX, (e.currentTarget as SVGRectElement).ownerSVGElement!.getBoundingClientRect());
            }}
            onPointerUp={(e) => {
              scrubberDragging.current = false;
              try { (e.target as Element).releasePointerCapture?.(e.pointerId); } catch { /* noop */ }
            }}
          />

          {/* Bars — on top so clicks/hovers hit them first */}
          <Group left={LANE_LABEL_WIDTH}>
            {turns.map((turn, i) => {
              const x = xScale(turn.startTs - t0);
              const w = Math.max(xScale(turn.hasLatency ? turn.latencyMs : 0), MIN_BAR_WIDTH);
              const y = laneY(turn.sessionId);
              const isActive = i === playhead.idx;
              const isPast = i < playhead.idx;
              const color = turn.isError
                ? "rgb(var(--error))"
                : `rgb(${roleColorVar(turn.agentRole)})`;
              const baseOpacity = isActive ? 1 : isPast ? 0.75 : 0.3;
              // Errors never dim — they must read as "this failed" at a glance.
              const opacity = turn.isError ? 1 : baseOpacity;
              const stroke = isActive
                ? "rgb(var(--fg-primary))"
                : turn.isError
                ? "rgb(var(--error))"
                : "transparent";
              const strokeWidth = isActive ? 1.5 : turn.isError ? 2 : 0;
              return (
                <g
                  key={turn.id}
                  onMouseEnter={(e) => handleBarEnter(turn, e)}
                  onMouseMove={handleBarMove}
                  onMouseLeave={handleBarLeave}
                  onClick={() => playhead.setIdx(i)}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    x={x} y={y} width={w} height={18} rx={3}
                    fill={color}
                    opacity={opacity}
                    stroke={stroke}
                    strokeWidth={strokeWidth}
                  />
                </g>
              );
            })}
          </Group>

          {/* Playhead line — turns red when the active turn is an error */}
          {currentTurn && (
            <Group left={LANE_LABEL_WIDTH}>
              <line
                x1={playheadX} x2={playheadX}
                y1={TOP_PADDING - 6} y2={TOP_PADDING + chartHeight + 2}
                stroke={`rgb(var(${currentTurn.isError ? "--error" : "--accent"}))`}
                strokeWidth="1.5"
                strokeDasharray="3 3"
              />
              <circle
                cx={playheadX} cy={TOP_PADDING - 6} r={4}
                fill={`rgb(var(${currentTurn.isError ? "--error" : "--accent"}))`}
              />
            </Group>
          )}

          {/* Axis */}
          <Group left={LANE_LABEL_WIDTH} top={TOP_PADDING + chartHeight + 2}>
            <AxisBottom
              scale={xScale}
              numTicks={6}
              tickFormat={(v) => formatDuration(Number(v))}
              stroke="rgb(var(--border))"
              tickStroke="rgb(var(--border))"
              tickLabelProps={() => ({
                fill: "rgb(var(--fg-muted))",
                fontSize: 10,
                textAnchor: "middle",
              })}
            />
          </Group>
        </svg>

        {tooltip && <TurnTooltip {...tooltip} />}
      </div>
    </div>
  );
}

function TurnTooltip({ turn, x, y }: TooltipState) {
  // Offset slightly so the tooltip doesn't sit under the cursor.
  const left = x + 12;
  const top = y + 12;
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-md border border-border bg-surface/95 px-3 py-2 text-xs shadow-lg backdrop-blur-sm min-w-[200px]"
      style={{ left, top }}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="font-mono text-fg-primary">turn {turn.turnNumber}</span>
        <span className="text-fg-muted">·</span>
        <span className="text-fg-secondary">{turn.agentRole}</span>
        {turn.isError && (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-[color:var(--error)]/20 text-[color:var(--error)]">
            error
          </span>
        )}
      </div>
      <div className="font-mono text-[10px] text-fg-muted truncate mb-1">
        {turn.sessionId}
      </div>
      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
        <span className="text-fg-muted">model</span>
        <span className="text-fg-secondary truncate">{turn.model ?? "—"}</span>
        <span className="text-fg-muted">status</span>
        <span className="text-fg-secondary tabular-nums">{turn.statusCode ?? "—"}</span>
        <span className="text-fg-muted">latency</span>
        <span className="text-fg-secondary tabular-nums">
          {turn.hasLatency ? formatDuration(turn.latencyMs) : "—"}
        </span>
        <span className="text-fg-muted">tokens</span>
        <span className="text-fg-secondary tabular-nums">
          {formatTokens(turn.inputTokens)} in · {formatTokens(turn.outputTokens)} out
        </span>
        <span className="text-fg-muted">cost</span>
        <span className="text-fg-secondary tabular-nums">{formatCost(turn.totalCostUsd)}</span>
        {turn.toolCalls.length > 0 && (
          <>
            <span className="text-fg-muted">tools</span>
            <span className="text-fg-secondary tabular-nums">{turn.toolCalls.length}</span>
          </>
        )}
      </div>
      {turn.error && (
        <div className="mt-1.5 pt-1.5 border-t border-border-soft text-[10px] text-[color:var(--error)] break-words">
          {turn.error.length > 140 ? turn.error.slice(0, 140) + "…" : turn.error}
        </div>
      )}
    </div>
  );
}
