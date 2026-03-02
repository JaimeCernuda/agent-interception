import { useEffect, useState } from "react";
import { getInteraction, downloadUrl } from "../api";
import type { Interaction } from "../types";
import Overview from "./detail/Overview";
import MetricsGrid from "./detail/MetricsGrid";
import Messages from "./detail/Messages";
import RequestResponse from "./detail/RequestResponse";
import StreamTimeline from "./detail/StreamTimeline";
import CopyButton from "./ui/CopyButton";

type Tab = "overview" | "metrics" | "messages" | "raw" | "stream";

interface Props {
  id: string;
  onClose: () => void;
}

export default function InteractionDrawer({ id, onClose }: Props) {
  const [interaction, setInteraction] = useState<Interaction | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

  useEffect(() => {
    setLoading(true);
    setError(null);
    setInteraction(null);
    getInteraction(id)
      .then((data) => {
        setInteraction(data);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [id]);

  const tabs: { key: Tab; label: string; hidden?: boolean }[] = [
    { key: "overview", label: "Overview" },
    { key: "metrics", label: "Metrics" },
    { key: "messages", label: "Messages" },
    { key: "raw", label: "Request/Response" },
    { key: "stream", label: "Stream", hidden: !interaction?.is_streaming },
  ];

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="fixed right-0 top-0 h-full w-full max-w-3xl bg-gray-900 border-l border-gray-800 z-50 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-xs text-gray-500 truncate font-mono">{id}</div>
          </div>
          <div className="flex items-center gap-2">
            {interaction && (
              <>
                <CopyButton text={JSON.stringify(interaction, null, 2)} />
                <a
                  href={downloadUrl(id)}
                  download
                  className="text-xs px-2 py-1 rounded border border-gray-700 hover:border-gray-500 text-gray-400 hover:text-gray-200 transition-colors"
                >
                  Download
                </a>
              </>
            )}
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-gray-200 text-lg leading-none px-2"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-800 px-1 shrink-0">
          {tabs
            .filter((t) => !t.hidden)
            .map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
                  tab === t.key
                    ? "border-blue-500 text-blue-400"
                    : "border-transparent text-gray-500 hover:text-gray-300"
                }`}
              >
                {t.label}
              </button>
            ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div className="text-gray-500 text-sm py-8 text-center">Loading…</div>
          )}
          {error && (
            <div className="text-red-400 text-sm py-4">Error: {error}</div>
          )}
          {interaction && (
            <>
              {tab === "overview" && <Overview interaction={interaction} />}
              {tab === "metrics" && <MetricsGrid interaction={interaction} />}
              {tab === "messages" && <Messages interaction={interaction} />}
              {tab === "raw" && <RequestResponse interaction={interaction} />}
              {tab === "stream" && <StreamTimeline interaction={interaction} />}
            </>
          )}
        </div>
      </div>
    </>
  );
}
