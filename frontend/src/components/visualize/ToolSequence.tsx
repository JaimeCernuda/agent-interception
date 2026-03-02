import { useState } from "react";
import type { ToolCallStep } from "../../types";

interface Props {
  steps: ToolCallStep[];
}

function JsonBlock({ value }: { value: unknown }) {
  const text =
    typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);

  // Collapse long values
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const isLong = lines.length > 6 || text.length > 300;

  const display = isLong && !expanded ? lines.slice(0, 6).join("\n") + "\n…" : text;

  return (
    <div className="relative">
      <pre className="text-xs font-mono text-gray-300 bg-gray-900 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
        {display}
      </pre>
      {isLong && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="text-xs text-blue-400 hover:underline mt-0.5"
        >
          {expanded ? "collapse" : "expand"}
        </button>
      )}
    </div>
  );
}

interface ToolCallRowProps {
  name: string | null;
  input: Record<string, unknown>;
  result?: string;
  globalIndex: number;
}

function ToolCallRow({ name, input, result, globalIndex }: ToolCallRowProps) {
  const [open, setOpen] = useState(false);
  const hasInput = Object.keys(input).length > 0;

  return (
    <div className="border border-gray-800 rounded overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-800/50 transition-colors"
      >
        {/* Step badge */}
        <span className="flex-shrink-0 w-6 h-6 rounded-full bg-orange-500/20 border border-orange-500/40 text-orange-300 text-xs flex items-center justify-center font-mono font-semibold">
          {globalIndex}
        </span>
        {/* Tool name */}
        <span className="flex-1 text-sm font-mono text-orange-300 font-medium truncate">
          {name ?? "<unknown>"}
        </span>
        {/* Input preview */}
        {hasInput && !open && (
          <span className="text-xs text-gray-500 truncate max-w-[200px] font-mono">
            {Object.entries(input)
              .slice(0, 2)
              .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
              .join(", ")}
          </span>
        )}
        {/* Result badge */}
        {result !== undefined && (
          <span className="flex-shrink-0 text-xs text-green-400 bg-green-900/20 border border-green-800/40 rounded px-1.5 py-0.5">
            result
          </span>
        )}
        {/* Chevron */}
        <span className="flex-shrink-0 text-gray-600 text-xs">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="border-t border-gray-800 px-3 py-2 space-y-2 bg-gray-950/40">
          {hasInput && (
            <div>
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                Input
              </div>
              <JsonBlock value={input} />
            </div>
          )}
          {!hasInput && (
            <div className="text-xs text-gray-600 italic">No input arguments</div>
          )}
          {result !== undefined && (
            <div>
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                Result
              </div>
              <JsonBlock value={result} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ToolSequence({ steps }: Props) {
  if (steps.length === 0) {
    return (
      <div className="text-gray-600 text-sm py-4 text-center">
        No tool calls found in this session.
      </div>
    );
  }

  let globalToolIndex = 1;

  return (
    <div className="space-y-4">
      {steps.map((step) => {
        // Map toolCallId → result content for this step's results
        const resultMap: Record<string, string> = {};
        for (const r of step.toolResults) {
          if (r.toolCallId) resultMap[r.toolCallId] = r.content;
        }

        return (
          <div key={step.interactionId} className="space-y-1.5">
            {/* Interaction header */}
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <span className="font-mono text-gray-600">
                {new Date(step.timestamp).toLocaleTimeString()}
              </span>
              {step.model && (
                <span className="text-gray-600 truncate">{step.model}</span>
              )}
              {step.latencyMs != null && (
                <span className="text-gray-700">{step.latencyMs.toFixed(0)}ms</span>
              )}
              {step.error && (
                <span className="text-red-400 truncate">{step.error}</span>
              )}
              {step.toolResults.length > 0 && step.toolCalls.length === 0 && (
                <span className="text-gray-600 italic">— tool results only</span>
              )}
            </div>

            {/* Tool results that arrived with this interaction (no new calls) */}
            {step.toolCalls.length === 0 && step.toolResults.length > 0 && (
              <div className="pl-3 border-l-2 border-green-800/40 space-y-1">
                {step.toolResults.map((r, ri) => (
                  <div key={ri} className="text-xs">
                    <div className="text-gray-500 mb-0.5">
                      Result{r.toolCallId ? ` for ${r.toolCallId.slice(0, 8)}…` : ""}:
                    </div>
                    <JsonBlock value={r.content} />
                  </div>
                ))}
              </div>
            )}

            {/* Tool calls */}
            {step.toolCalls.map((tc, i) => {
              const result = tc.id ? resultMap[tc.id] : undefined;
              const idx = globalToolIndex++;
              return (
                <ToolCallRow
                  key={i}
                  name={tc.name}
                  input={tc.input}
                  result={result}
                  globalIndex={idx}
                />
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
