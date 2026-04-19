# Why the Multi-Agent Graph Doesn't Work with `claude_agent_sdk`

## The Short Answer

The multi-agent graph view requires two custom HTTP headers to be sent with every
request. The `claude_agent_sdk` wraps the Claude CLI and has no way to inject
custom headers. The direct Anthropic Python SDK does — which is why the API key
approach works.

---

## How the Graph Is Built

The interceptor proxy builds the multi-agent graph by grouping interactions into
conversations and detecting handoffs between agents. It relies on two headers:

| Header | Purpose |
|---|---|
| `X-Interceptor-Conversation-Id` | Groups all agent sessions into one conversation |
| `X-Agent-Role` | Labels each node as `orchestrator`, `subagent`, or `tool` |

When a request arrives, the proxy checks for `X-Interceptor-Conversation-Id`
(`handler.py` line 131). If the header is present, the interaction is linked to
that conversation. If the session ID changes between consecutive turns in the same
conversation, the store marks it as a `"handoff"` turn type — and handoffs are
what become edges in the graph (`store.py`, `_resolve_threading`).

Without these headers, every session gets its own isolated `conversation_id` (a
random UUID generated at first call), and no edges are ever created between them.
The result is what you saw: **9 isolated single-node graphs instead of one
connected hub-and-spoke graph**.

---

## Why `claude_agent_sdk` Can't Set These Headers

`claude_agent_sdk` is a Python wrapper around the **Claude CLI** (`claude -p`).
When you call `query()`, the SDK spawns a subprocess:

```
python  →  claude_agent_sdk.query()  →  subprocess: claude -p "..."
```

The Claude CLI binary reads one environment variable to know where to send
requests:

```
ANTHROPIC_BASE_URL=http://127.0.0.1:8080/_session/{session_id}
```

That's the only configuration surface the CLI exposes for HTTP routing. It does
**not** support setting arbitrary request headers. There is no `--header` flag,
no env var for custom headers, and no way to intercept the HTTP calls the binary
makes internally.

This means no matter how you configure `claude_agent_sdk`, the proxy will never
receive `X-Interceptor-Conversation-Id` or `X-Agent-Role` from a CLI-based agent.

---

## Why the Anthropic Python SDK Works

The `anthropic` Python SDK makes HTTP calls directly from your Python process.
When you create a client you can set `default_headers`:

```python
client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=f"{PROXY_URL}/_session/{session_id}",
    default_headers={
        "X-Agent-Role":                  "orchestrator",
        "X-Interceptor-Conversation-Id": conv_id,
    },
)
```

Every `client.messages.create()` call sends these headers to the proxy. The proxy
groups the interactions correctly, detects handoffs, and the multi-agent graph
renders as expected.

---

## Can I Still Use `claude_agent_sdk` and See the Graph?

Yes, but it requires a workaround: a **header-injector proxy** — a small local
HTTP server (Starlette + uvicorn, both already project dependencies) that sits
between the Claude CLI and the real interceptor proxy.

```
Claude CLI  →  HeaderInjector (127.0.0.1:PORT)  →  Interceptor Proxy:8080  →  Anthropic
```

The injector adds the two missing headers to every forwarded request. Each agent
points its `ANTHROPIC_BASE_URL` at the injector, which is reconfigured with the
correct `role` and shared `conversation_id` before each agent starts.

This approach keeps the real Claude CLI tool-use capabilities (file reads, bash
commands, etc.) while still producing a properly linked multi-agent graph. The
tradeoff is extra complexity — an embedded proxy server — compared to the clean
`default_headers` option of the direct SDK.

---

## Summary

| | `claude_agent_sdk` | Anthropic SDK (API key) |
|---|---|---|
| Tool use | Real CLI tools (Read, Glob, Bash…) | Custom Python implementations |
| Custom headers | ✗ Not possible | ✓ `default_headers` on client |
| Multi-agent graph | ✗ Broken (isolated sessions) | ✓ Works out of the box |
| API key required | No (uses CLI auth) | Yes (from env / `.env` file) |
| Workaround available | Header-injector proxy | — |

**The recommended approach for multi-agent visualization is the Anthropic Python
SDK with the API key read from the environment (`ANTHROPIC_API_KEY` in
`scripts/.env`), never hardcoded.**
