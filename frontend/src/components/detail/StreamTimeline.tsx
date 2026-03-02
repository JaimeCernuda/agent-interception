import { useState } from "react";
import type { Interaction, StreamChunk } from "../../types";

const PREVIEW_LEN = 120;

function chunkEventType(chunk: StreamChunk, provider: string): string {
  if (!chunk.parsed) return "raw";
  const p = chunk.parsed;
  if (provider === "anthropic" && p.type) return String(p.type);
  if (provider === "openai") {
    const choices = p.choices;
    if (Array.isArray(choices) && choices.length > 0) {
      const delta = (choices[0] as Record<string, unknown>).delta as Record<string, unknown> | undefined;
      if (delta?.tool_calls) return "tool_call_delta";
      if (delta?.content) return "content_delta";
    }
    if (p.usage) return "usage";
    return "chunk";
  }
  if (provider === "ollama" && p.done) return "done";
  return String(p.type ?? "chunk");
}

function fmtRelMs(first: number, current: number): string {
  const delta = current - first;
  return `+${delta.toFixed(0)}ms`;
}

function ChunkRow({ chunk, provider, firstMs }: { chunk: StreamChunk; provider: string; firstMs: number }) {
  const [expanded, setExpanded] = useState(false);
  const ts = new Date(chunk.timestamp).getTime();
  const preview = chunk.data.length > PREVIEW_LEN ? chunk.data.slice(0, PREVIEW_LEN) + "…" : chunk.data;
  const eventType = chunkEventType(chunk, provider);

  return (
    <div
      className="border-b border-gray-800/40 hover:bg-gray-800/30 cursor-pointer"
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-baseline gap-3 px-2 py-1.5 text-xs">
        <span className="text-gray-600 font-mono w-6 text-right shrink-0">{chunk.index}</span>
        <span className="text-gray-500 font-mono whitespace-nowrap">{isNaN(ts) ? "?" : fmtRelMs(firstMs, ts)}</span>
        <span className="text-blue-400 font-mono whitespace-nowrap">{eventType}</span>
        <span className="text-gray-600 font-mono whitespace-nowrap">{chunk.data.length}B</span>
        {chunk.delta_text && (
          <span className="text-green-400 truncate">{chunk.delta_text.slice(0, 40)}</span>
        )}
        <span className="text-gray-600 font-mono truncate hidden sm:block">{preview}</span>
      </div>
      {expanded && (
        <pre className="mx-2 mb-2 p-2 bg-gray-900 rounded border border-gray-700 text-xs font-mono text-gray-200 overflow-x-auto whitespace-pre-wrap">
          {chunk.data}
        </pre>
      )}
    </div>
  );
}

export default function StreamTimeline({ interaction: i }: { interaction: Interaction }) {
  const chunks = i.stream_chunks;

  if (!i.is_streaming || chunks.length === 0) {
    return (
      <div className="text-gray-600 text-sm py-4">No stream chunks recorded.</div>
    );
  }

  const firstMs = new Date(chunks[0].timestamp).getTime();
  const lastMs = new Date(chunks[chunks.length - 1].timestamp).getTime();
  const streamDuration = isNaN(firstMs) || isNaN(lastMs) ? null : lastMs - firstMs;

  return (
    <div>
      {/* Summary row */}
      <div className="flex gap-6 text-xs mb-4 flex-wrap">
        <span className="text-gray-500">
          <span className="text-gray-300 font-semibold">{chunks.length}</span> chunks
        </span>
        {i.time_to_first_token_ms != null && (
          <span className="text-gray-500">
            TTFT: <span className="text-gray-300 font-semibold">{i.time_to_first_token_ms.toFixed(1)}ms</span>
          </span>
        )}
        {streamDuration != null && (
          <span className="text-gray-500">
            Stream duration: <span className="text-gray-300 font-semibold">{streamDuration.toFixed(0)}ms</span>
          </span>
        )}
        {i.response_text && (
          <span className="text-gray-500">
            Reconstructed: <span className="text-gray-300 font-semibold">{i.response_text.length.toLocaleString()} chars</span>
          </span>
        )}
      </div>

      {/* Header */}
      <div className="flex gap-3 px-2 py-1 text-xs text-gray-600 border-b border-gray-800 font-medium">
        <span className="w-6 text-right shrink-0">#</span>
        <span className="w-16">Offset</span>
        <span className="w-24">Event</span>
        <span className="w-12">Size</span>
        <span>Delta / Preview</span>
      </div>

      {/* Scrollable list */}
      <div className="overflow-y-auto max-h-[50vh]">
        {chunks.map((chunk) => (
          <ChunkRow
            key={chunk.index}
            chunk={chunk}
            provider={i.provider}
            firstMs={firstMs}
          />
        ))}
      </div>
    </div>
  );
}
