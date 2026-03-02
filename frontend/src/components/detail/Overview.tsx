import type { Interaction } from "../../types";

interface Props {
  interaction: Interaction;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value == null || value === "" || value === false) return null;
  return (
    <tr className="border-b border-gray-800/40">
      <td className="py-2 pr-4 text-xs text-gray-500 font-medium whitespace-nowrap align-top w-40">
        {label}
      </td>
      <td className="py-2 text-sm text-gray-200 font-mono break-all">{value}</td>
    </tr>
  );
}

export default function Overview({ interaction: i }: Props) {
  const cost = i.cost_estimate;

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <tbody>
          <Row label="ID" value={i.id} />
          <Row label="Session" value={i.session_id} />
          <Row
            label="Timestamp"
            value={new Date(i.timestamp).toLocaleString()}
          />
          <Row label="Provider" value={i.provider} />
          <Row label="Model" value={i.model} />
          <Row label="Method" value={i.method} />
          <Row label="Path" value={i.path} />
          <Row label="Status" value={i.status_code} />
          <Row label="Streaming" value={i.is_streaming ? "Yes" : null} />
          <Row
            label="Latency"
            value={i.total_latency_ms != null ? `${i.total_latency_ms.toFixed(1)} ms` : null}
          />
          <Row
            label="TTFT"
            value={
              i.time_to_first_token_ms != null
                ? `${i.time_to_first_token_ms.toFixed(1)} ms`
                : null
            }
          />
          <Row
            label="Input tokens"
            value={i.token_usage?.input_tokens}
          />
          <Row
            label="Output tokens"
            value={i.token_usage?.output_tokens}
          />
          <Row
            label="Cache creation"
            value={i.token_usage?.cache_creation_tokens}
          />
          <Row
            label="Cache read"
            value={i.token_usage?.cache_read_tokens}
          />
          <Row
            label="Cost (USD)"
            value={cost ? `$${cost.total_cost.toFixed(6)}` : null}
          />
          <Row
            label="Chunks"
            value={i.stream_chunks.length > 0 ? i.stream_chunks.length : null}
          />
          <Row label="Error" value={i.error} />
          <Row
            label="Tools defined"
            value={i.tools ? i.tools.length : null}
          />
          <Row
            label="Images"
            value={i.image_metadata ? `${i.image_metadata.count} image(s)` : null}
          />
        </tbody>
      </table>
    </div>
  );
}
