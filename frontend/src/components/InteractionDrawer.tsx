import { useEffect, useState } from "react";
import { getInteraction, downloadUrl } from "../api";
import type { Interaction } from "../types";
import SummaryTab from "./detail/SummaryTab";
import Messages from "./detail/Messages";
import ToolsTab from "./detail/ToolsTab";
import RawTab from "./detail/RawTab";
import ErrorBanner from "./detail/ErrorBanner";
import CopyButton from "./ui/CopyButton";

type Tab = "summary" | "messages" | "tools" | "raw";

interface Props {
  id: string;
  onClose: () => void;
}

export default function InteractionDrawer({ id, onClose }: Props) {
  const [interaction, setInteraction] = useState<Interaction | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("summary");

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

  const tabs: { key: Tab; label: string }[] = [
    { key: "summary",  label: "Summary" },
    { key: "messages", label: "Messages" },
    { key: "tools",    label: "Tools" },
    { key: "raw",      label: "Raw" },
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
      <div className="fixed right-0 top-0 h-full w-full max-w-3xl bg-elevate border-l border-border z-50 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-xs text-fg-secondary truncate font-mono">{id}</div>
          </div>
          <div className="flex items-center gap-2">
            {interaction && (
              <>
                <CopyButton text={JSON.stringify(interaction, null, 2)} />
                <a
                  href={downloadUrl(id)}
                  download
                  className="text-xs px-2 py-1 rounded border border-border hover:border-fg-muted text-fg-secondary hover:text-fg-primary transition-colors"
                >
                  Download
                </a>
              </>
            )}
            <button
              onClick={onClose}
              className="text-fg-secondary hover:text-fg-primary text-lg leading-none px-2"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border px-1 shrink-0">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
                tab === t.key
                  ? "border-accent text-fg-primary"
                  : "border-transparent text-fg-secondary hover:text-fg-primary"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div className="text-fg-secondary text-sm py-8 text-center">Loading…</div>
          )}
          {error && (
            <div className="text-error text-sm py-4">Error: {error}</div>
          )}
          {interaction && (
            <>
              <ErrorBanner error={interaction.error} />
              {tab === "summary"  && <SummaryTab interaction={interaction} />}
              {tab === "messages" && <Messages   interaction={interaction} />}
              {tab === "tools"    && <ToolsTab   interaction={interaction} />}
              {tab === "raw"      && <RawTab     interaction={interaction} />}
            </>
          )}
        </div>
      </div>
    </>
  );
}
