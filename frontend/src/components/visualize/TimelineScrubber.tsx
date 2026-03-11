import { useRef, useState, useCallback, useMemo } from "react";
import type { TimelineEntry, ToolCallStep } from "../../types";

const HEIGHT = 64;
const DOT_R = 5;
const RESP_R = 4;
const PADDING = 16;

function dotColor(entry: TimelineEntry): string {
  if (entry.error || (entry.status != null && entry.status >= 400)) return "#f87171";
  if (entry.status != null && entry.status >= 300) return "#fbbf24";
  return "#34d399";
}

interface Props {
  entries: TimelineEntry[];
  toolSequence?: ToolCallStep[];
  onRangeChange?: (from: string | null, to: string | null) => void;
}

export default function TimelineScrubber({ entries, toolSequence, onRangeChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(800);
  const [selection, setSelection] = useState<{ startX: number; endX: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState(0);
  const [hovered, setHovered] = useState<TimelineEntry | null>(null);
  const [hoveredX, setHoveredX] = useState(0);
  const [pinned, setPinned] = useState<TimelineEntry | null>(null);
  const [pinnedX, setPinnedX] = useState(0);

  const resizeRef = useCallback((node: SVGSVGElement | null) => {
    if (!node) return;
    (svgRef as React.MutableRefObject<SVGSVGElement | null>).current = node;
    const obs = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width);
    });
    obs.observe(node.parentElement!);
  }, []);

  // Build map: interactionId → tool names
  const toolNameMap = useMemo(() => {
    const map: Record<string, string[]> = {};
    if (toolSequence) {
      for (const step of toolSequence) {
        if (step.toolCalls.length > 0) {
          map[step.interactionId] = step.toolCalls.map(tc => tc.name ?? "?");
        }
      }
    }
    return map;
  }, [toolSequence]);

  if (entries.length === 0) return null;

  const times = entries.map((e) => new Date(e.timestamp).getTime());
  const minT = Math.min(...times);
  // For maxT, also consider response end times
  const endTimes = entries.map((e, i) => times[i] + (e.latencyMs ?? 0));
  const maxT = Math.max(...times, ...endTimes);
  const range = maxT - minT || 1;

  const toX = (t: number) => PADDING + ((t - minT) / range) * (width - PADDING * 2);
  const toT = (x: number) => minT + ((x - PADDING) / (width - PADDING * 2)) * range;

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    setDragStart(x);
    setDragging(true);
    setSelection({ startX: x, endX: x });
  }

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (dragging) {
      setSelection({ startX: dragStart, endX: x });
    }
  }

  function handleMouseUp() {
    setDragging(false);
    if (!selection) return;
    const x1 = Math.min(selection.startX, selection.endX);
    const x2 = Math.max(selection.startX, selection.endX);
    if (x2 - x1 < 4) {
      setSelection(null);
      onRangeChange?.(null, null);
      return;
    }
    const t1 = toT(x1);
    const t2 = toT(x2);
    onRangeChange?.(new Date(t1).toISOString(), new Date(t2).toISOString());
  }

  const selX1 = selection ? Math.min(selection.startX, selection.endX) : null;
  const selX2 = selection ? Math.max(selection.startX, selection.endX) : null;

  const activeEntry = pinned ?? hovered;
  const activeX = pinned ? pinnedX : hoveredX;

  return (
    <div className="relative select-none">
      <div className="text-xs text-gray-500 mb-1 flex items-center gap-3">
        <span>Timeline ({entries.length} calls)</span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full" style={{ background: "#34d399" }} />
          Call start
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full" style={{ background: "#f97316" }} />
          Response end
        </span>
        {pinned && (
          <button
            className="text-blue-400 hover:text-blue-300 underline"
            onClick={() => setPinned(null)}
          >
            Unpin
          </button>
        )}
        {selection && (
          <button
            className="underline hover:text-gray-300"
            onClick={() => { setSelection(null); onRangeChange?.(null, null); }}
          >
            Clear selection
          </button>
        )}
      </div>

      <svg
        ref={resizeRef}
        width="100%"
        height={HEIGHT}
        className="rounded border border-gray-700 bg-gray-900 cursor-crosshair overflow-visible"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => { setDragging(false); setHovered(null); }}
      >
        {/* Selection rect */}
        {selX1 != null && selX2 != null && (
          <rect x={selX1} y={0} width={selX2 - selX1} height={HEIGHT}
            fill="rgba(59,130,246,0.15)" stroke="#3b82f6" strokeWidth={1} />
        )}

        {/* Center axis line */}
        <line x1={PADDING} y1={HEIGHT / 2} x2={width - PADDING} y2={HEIGHT / 2}
          stroke="#1f2937" strokeWidth={1} />

        {/* Entries */}
        {entries.map((entry, i) => {
          const startX = toX(times[i]);
          const endT = times[i] + (entry.latencyMs ?? 0);
          const endX = entry.latencyMs != null ? Math.min(toX(endT), width - PADDING + 10) : null;
          const callColor = dotColor(entry);
          const isActive = activeEntry?.interactionId === entry.interactionId;

          return (
            <g
              key={entry.interactionId}
              onClick={(e) => {
                e.stopPropagation();
                if (pinned?.interactionId === entry.interactionId) {
                  setPinned(null);
                } else {
                  setPinned(entry);
                  setPinnedX(startX);
                }
              }}
              onMouseEnter={(e) => {
                if (!pinned) {
                  setHovered(entry);
                  setHoveredX(startX);
                }
                e.stopPropagation();
              }}
              onMouseLeave={() => { if (!pinned) setHovered(null); }}
              style={{ cursor: "pointer" }}
            >
              {/* Duration bar */}
              {endX != null && endX > startX + 2 && (
                <line
                  x1={startX} y1={HEIGHT / 2}
                  x2={endX}   y2={HEIGHT / 2}
                  stroke={callColor}
                  strokeWidth={isActive ? 2.5 : 1.5}
                  strokeOpacity={0.35}
                />
              )}

              {/* Call start dot (green/red) */}
              <circle
                cx={startX} cy={HEIGHT / 2}
                r={isActive ? DOT_R + 1.5 : DOT_R}
                fill={callColor}
                opacity={0.9}
              />

              {/* Response end dot (orange) */}
              {endX != null && (
                <circle
                  cx={endX} cy={HEIGHT / 2}
                  r={isActive ? RESP_R + 1 : RESP_R}
                  fill="#f97316"
                  opacity={0.8}
                />
              )}

              {/* Pinned indicator */}
              {pinned?.interactionId === entry.interactionId && (
                <line x1={startX} y1={4} x2={startX} y2={HEIGHT - 4}
                  stroke="#60a5fa" strokeWidth={1.5} strokeDasharray="3 2" opacity={0.6} />
              )}
            </g>
          );
        })}

        {/* Time labels */}
        <text x={PADDING} y={HEIGHT - 4} fontSize={9} fill="#4b5563">
          {new Date(minT).toLocaleTimeString()}
        </text>
        <text x={width - PADDING} y={HEIGHT - 4} textAnchor="end" fontSize={9} fill="#4b5563">
          {new Date(maxT).toLocaleTimeString()}
        </text>
      </svg>

      {/* Tooltip */}
      {activeEntry && (
        <div
          className="absolute z-20 bg-gray-800 border border-gray-600 rounded px-2 py-1.5 text-xs pointer-events-none shadow-lg"
          style={{ left: Math.min(activeX, width - 180), top: -100 }}
        >
          <div className="text-gray-300 font-semibold">{activeEntry.provider}</div>
          <div className="text-gray-400">{new Date(activeEntry.timestamp).toLocaleTimeString()}</div>
          <div className={activeEntry.status && activeEntry.status >= 400 ? "text-red-400" : "text-green-400"}>
            {activeEntry.status ?? "?"} · {activeEntry.latencyMs != null ? `${activeEntry.latencyMs.toFixed(0)}ms` : "—"}
          </div>
          {(() => {
            const names = toolNameMap[activeEntry.interactionId];
            return names && names.length > 0 ? (
              <div className="text-orange-400 mt-0.5">
                Tools: {names.join(", ")}
              </div>
            ) : null;
          })()}
          {activeEntry.error && (
            <div className="text-red-300 truncate max-w-[160px] mt-0.5">{activeEntry.error}</div>
          )}
          {pinned?.interactionId === activeEntry.interactionId ? (
            <div className="text-gray-600 mt-0.5">Click to unpin</div>
          ) : (
            <div className="text-gray-600 mt-0.5">Click to pin</div>
          )}
        </div>
      )}
    </div>
  );
}
