import { useRef, useState, useCallback } from "react";
import type { TimelineEntry } from "../../types";

const HEIGHT = 56;
const DOT_R = 5;
const PADDING = 16;

function dotColor(entry: TimelineEntry): string {
  if (entry.error || (entry.status != null && entry.status >= 400)) return "#f87171"; // red
  if (entry.status != null && entry.status >= 300) return "#fbbf24"; // yellow
  return "#34d399"; // green
}

interface Props {
  entries: TimelineEntry[];
  onRangeChange?: (from: string | null, to: string | null) => void;
}

export default function TimelineScrubber({ entries, onRangeChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(800);
  const [selection, setSelection] = useState<{ startX: number; endX: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState(0);
  const [hovered, setHovered] = useState<TimelineEntry | null>(null);
  const [tooltipX, setTooltipX] = useState(0);

  const resizeRef = useCallback((node: SVGSVGElement | null) => {
    if (!node) return;
    (svgRef as React.MutableRefObject<SVGSVGElement | null>).current = node;
    const obs = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width);
    });
    obs.observe(node.parentElement!);
  }, []);

  if (entries.length === 0) return null;

  const times = entries.map((e) => new Date(e.timestamp).getTime());
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
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

  function handleMouseUp(_e: React.MouseEvent<SVGSVGElement>) {
    setDragging(false);
    if (!selection) return;
    const x1 = Math.min(selection.startX, selection.endX);
    const x2 = Math.max(selection.startX, selection.endX);
    if (x2 - x1 < 4) {
      // Click - clear selection
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

  return (
    <div className="relative select-none">
      <div className="text-xs text-gray-500 mb-1">
        Timeline ({entries.length} calls)
        {selection && (
          <button
            className="ml-2 underline hover:text-gray-300"
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
          <rect
            x={selX1}
            y={0}
            width={selX2 - selX1}
            height={HEIGHT}
            fill="rgba(59,130,246,0.15)"
            stroke="#3b82f6"
            strokeWidth={1}
          />
        )}

        {/* Dots */}
        {entries.map((entry, i) => {
          const x = toX(times[i]);
          const y = HEIGHT / 2;
          return (
            <circle
              key={entry.interactionId}
              cx={x}
              cy={y}
              r={DOT_R}
              fill={dotColor(entry)}
              opacity={0.85}
              onMouseEnter={(e) => {
                setHovered(entry);
                setTooltipX(x);
                e.stopPropagation();
              }}
              onMouseLeave={() => setHovered(null)}
            />
          );
        })}

        {/* Time labels */}
        <text x={PADDING} y={HEIGHT - 4} className="text-gray-600" fontSize={9} fill="#4b5563">
          {new Date(minT).toLocaleTimeString()}
        </text>
        <text x={width - PADDING} y={HEIGHT - 4} textAnchor="end" fontSize={9} fill="#4b5563">
          {new Date(maxT).toLocaleTimeString()}
        </text>
      </svg>

      {/* Tooltip */}
      {hovered && (
        <div
          className="absolute z-20 bg-gray-800 border border-gray-600 rounded px-2 py-1.5 text-xs pointer-events-none shadow-lg"
          style={{ left: Math.min(tooltipX, width - 160), top: -80 }}
        >
          <div className="text-gray-300">{hovered.provider}</div>
          <div className="text-gray-400">{new Date(hovered.timestamp).toLocaleTimeString()}</div>
          <div className={hovered.status && hovered.status >= 400 ? "text-red-400" : "text-green-400"}>
            {hovered.status ?? "?"} · {hovered.latencyMs != null ? `${hovered.latencyMs.toFixed(0)}ms` : "—"}
          </div>
          {hovered.error && <div className="text-red-300 truncate max-w-[140px]">{hovered.error}</div>}
        </div>
      )}
    </div>
  );
}
