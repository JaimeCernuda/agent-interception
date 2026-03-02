import type { Interaction } from "../../types";

const ROLE_STYLES: Record<string, string> = {
  system: "bg-purple-900/50 text-purple-300 border-purple-700",
  user: "bg-blue-900/30 text-blue-300 border-blue-800",
  assistant: "bg-emerald-900/30 text-emerald-300 border-emerald-800",
  tool: "bg-orange-900/30 text-orange-300 border-orange-800",
};

function RoleBadge({ role }: { role: string }) {
  const cls = ROLE_STYLES[role] ?? "bg-gray-800 text-gray-400 border-gray-700";
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-medium ${cls}`}>
      {role}
    </span>
  );
}

function ContentBlock({ content }: { content: unknown }) {
  if (content == null) return null;
  if (typeof content === "string") {
    // Detect code fences
    if (content.includes("```")) {
      const parts = content.split(/(```[\s\S]*?```)/g);
      return (
        <div className="text-sm text-gray-200 whitespace-pre-wrap space-y-1">
          {parts.map((part, i) =>
            part.startsWith("```") ? (
              <pre key={i} className="bg-gray-900 rounded p-2 text-xs font-mono overflow-x-auto border border-gray-700">
                {part.replace(/^```\w*\n?/, "").replace(/```$/, "")}
              </pre>
            ) : (
              <span key={i}>{part}</span>
            )
          )}
        </div>
      );
    }
    return <p className="text-sm text-gray-200 whitespace-pre-wrap">{content}</p>;
  }
  if (Array.isArray(content)) {
    return (
      <div className="space-y-1">
        {content.map((block, i) => {
          if (typeof block === "string") return <ContentBlock key={i} content={block} />;
          if (block && typeof block === "object") {
            const b = block as Record<string, unknown>;
            if (b.type === "text") return <ContentBlock key={i} content={b.text} />;
            if (b.type === "image") {
              return (
                <div key={i} className="text-xs text-gray-500 italic bg-gray-800 rounded px-2 py-1 border border-gray-700">
                  [Image: {String(b.media_type ?? "unknown")}, ~{String(b.approximate_size ?? "?")} bytes]
                </div>
              );
            }
            if (b.type === "tool_use") {
              return (
                <div key={i} className="rounded border border-orange-700/50 bg-orange-900/20 p-2 text-xs">
                  <div className="font-semibold text-orange-300 mb-1">Tool: {String(b.name)}</div>
                  <pre className="font-mono text-orange-200/80 text-xs overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(b.input, null, 2)}
                  </pre>
                </div>
              );
            }
            if (b.type === "tool_result") {
              return (
                <div key={i} className="rounded border border-yellow-700/50 bg-yellow-900/20 p-2 text-xs">
                  <div className="font-semibold text-yellow-300 mb-1">Tool result (id: {String(b.tool_use_id)})</div>
                  <ContentBlock content={b.content} />
                </div>
              );
            }
            if (b.type === "thinking") {
              return (
                <div key={i} className="rounded border border-gray-600 bg-gray-800/50 p-2 text-xs text-gray-400 italic">
                  [thinking]: {String(b.thinking ?? "")}
                </div>
              );
            }
          }
          return (
            <pre key={i} className="text-xs font-mono text-gray-400 overflow-x-auto">
              {JSON.stringify(block, null, 2)}
            </pre>
          );
        })}
      </div>
    );
  }
  return (
    <pre className="text-xs font-mono text-gray-400 overflow-x-auto whitespace-pre-wrap">
      {JSON.stringify(content, null, 2)}
    </pre>
  );
}

interface MessageBubble {
  role: string;
  content: unknown;
  name?: string;
}

function Bubble({ msg }: { msg: MessageBubble }) {
  return (
    <div className="flex gap-3 py-2 border-b border-gray-800/50">
      <div className="pt-0.5 shrink-0">
        <RoleBadge role={msg.role} />
      </div>
      <div className="flex-1 min-w-0">
        {msg.name && (
          <div className="text-xs text-gray-500 mb-1">name: {msg.name}</div>
        )}
        <ContentBlock content={msg.content} />
      </div>
    </div>
  );
}

export default function Messages({ interaction: i }: { interaction: Interaction }) {
  const messages: MessageBubble[] = [];

  if (i.system_prompt) {
    messages.push({ role: "system", content: i.system_prompt });
  }

  if (i.messages) {
    for (const msg of i.messages) {
      const m = msg as Record<string, unknown>;
      messages.push({
        role: String(m.role ?? "unknown"),
        content: m.content,
        name: m.name ? String(m.name) : undefined,
      });
    }
  }

  // Assistant response
  if (i.response_text || (i.tool_calls && i.tool_calls.length > 0)) {
    const assistantContent: unknown[] = [];
    if (i.response_text) assistantContent.push(i.response_text);
    if (i.tool_calls) {
      for (const tc of i.tool_calls) {
        assistantContent.push(tc);
      }
    }
    messages.push({ role: "assistant", content: assistantContent.length === 1 ? assistantContent[0] : assistantContent });
  }

  if (messages.length === 0) {
    return <div className="text-gray-600 text-sm py-4">No messages available.</div>;
  }

  return (
    <div className="space-y-0">
      {messages.map((msg, idx) => (
        <Bubble key={idx} msg={msg} />
      ))}
    </div>
  );
}
