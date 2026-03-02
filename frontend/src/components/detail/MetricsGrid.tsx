import { useState } from "react";
import type { Interaction } from "../../types";
import JsonViewer from "../ui/JsonViewer";

interface Card {
  label: string;
  value: string;
  sub?: string;
}

interface Section {
  title: string;
  cards: Card[];
}

function MetricCard({ card }: { card: Card }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3 border border-gray-700">
      <div className="text-xs text-gray-500 mb-1">{card.label}</div>
      <div className="text-sm font-semibold text-gray-100">{card.value}</div>
      {card.sub && <div className="text-xs text-gray-500 mt-0.5">{card.sub}</div>}
    </div>
  );
}

function SectionBlock({ section }: { section: Section }) {
  if (section.cards.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
        {section.title}
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
        {section.cards.map((c) => (
          <MetricCard key={c.label} card={c} />
        ))}
      </div>
    </div>
  );
}

function fmt(ms: number | null) {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(1)}ms`;
}

export default function MetricsGrid({ interaction: i }: { interaction: Interaction }) {
  const [showRaw, setShowRaw] = useState(false);

  const latencyCards: Card[] = [];
  if (i.total_latency_ms != null) latencyCards.push({ label: "Total latency", value: fmt(i.total_latency_ms)! });
  if (i.time_to_first_token_ms != null) latencyCards.push({ label: "TTFT", value: fmt(i.time_to_first_token_ms)! });

  const tu = i.token_usage;
  const tokenCards: Card[] = [];
  if (tu) {
    if (tu.input_tokens != null) tokenCards.push({ label: "Input tokens", value: String(tu.input_tokens) });
    if (tu.output_tokens != null) tokenCards.push({ label: "Output tokens", value: String(tu.output_tokens) });
    const total = tu.total_tokens ?? (tu.input_tokens ?? 0) + (tu.output_tokens ?? 0);
    if (total) tokenCards.push({ label: "Total tokens", value: String(total) });
    if (tu.cache_creation_tokens) tokenCards.push({ label: "Cache creation", value: String(tu.cache_creation_tokens) });
    if (tu.cache_read_tokens) tokenCards.push({ label: "Cache read", value: String(tu.cache_read_tokens) });
  }
  const ce = i.cost_estimate;
  if (ce && ce.total_cost) {
    tokenCards.push({ label: "Total cost", value: `$${ce.total_cost.toFixed(6)}`, sub: ce.note ?? undefined });
    if (ce.input_cost) tokenCards.push({ label: "Input cost", value: `$${ce.input_cost.toFixed(6)}` });
    if (ce.output_cost) tokenCards.push({ label: "Output cost", value: `$${ce.output_cost.toFixed(6)}` });
  }

  const streamCards: Card[] = [];
  if (i.is_streaming) {
    streamCards.push({ label: "Streaming", value: "Yes" });
    if (i.stream_chunks.length > 0) {
      streamCards.push({ label: "Chunks", value: String(i.stream_chunks.length) });
      const first = new Date(i.stream_chunks[0].timestamp).getTime();
      const last = new Date(i.stream_chunks[i.stream_chunks.length - 1].timestamp).getTime();
      if (!isNaN(first) && !isNaN(last)) {
        streamCards.push({ label: "Stream duration", value: fmt(last - first)! });
      }
      const textLen = i.response_text?.length ?? 0;
      if (textLen) streamCards.push({ label: "Reconstructed length", value: `${textLen.toLocaleString()} chars` });
    }
  }

  const payloadCards: Card[] = [];
  const reqSize = i.raw_request_body?.length;
  const resSize = i.raw_response_body?.length;
  if (reqSize) payloadCards.push({ label: "Request body", value: `${reqSize.toLocaleString()} B` });
  if (resSize) payloadCards.push({ label: "Response body", value: `${resSize.toLocaleString()} B` });

  const toolCards: Card[] = [];
  if (i.tools?.length) toolCards.push({ label: "Tools defined", value: String(i.tools.length) });
  if (i.tool_calls?.length) toolCards.push({ label: "Tool calls made", value: String(i.tool_calls.length) });

  const errorCards: Card[] = [];
  if (i.error) errorCards.push({ label: "Error", value: i.error });

  const imgCards: Card[] = [];
  if (i.image_metadata) {
    imgCards.push({ label: "Images", value: String(i.image_metadata.count) });
    if (i.image_metadata.media_types.length) {
      imgCards.push({ label: "Types", value: i.image_metadata.media_types.join(", ") });
    }
  }

  const sections: Section[] = [
    { title: "Latency & Timing", cards: latencyCards },
    { title: "Tokens & Cost", cards: tokenCards },
    { title: "Streaming", cards: streamCards },
    { title: "Payload Size", cards: payloadCards },
    { title: "Tools", cards: toolCards },
    { title: "Errors", cards: errorCards },
    { title: "Images", cards: imgCards },
  ];

  // Raw metrics
  const rawMetrics: Record<string, unknown> = {
    total_latency_ms: i.total_latency_ms,
    time_to_first_token_ms: i.time_to_first_token_ms,
    token_usage: i.token_usage,
    cost_estimate: i.cost_estimate,
    is_streaming: i.is_streaming,
    stream_chunk_count: i.stream_chunks.length,
    status_code: i.status_code,
    provider: i.provider,
    model: i.model,
  };

  return (
    <div className="space-y-2">
      {sections.map((s) => (
        <SectionBlock key={s.title} section={s} />
      ))}
      <div>
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="text-xs text-gray-500 hover:text-gray-300 underline mb-2"
        >
          {showRaw ? "Hide" : "Show"} raw metrics
        </button>
        {showRaw && <JsonViewer data={rawMetrics} initiallyExpanded />}
      </div>
    </div>
  );
}
