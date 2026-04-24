# Project summary — Semantic instrumentation for agentic AI


## The base `agent-interception` project 

### What it is

A transparent HTTP reverse proxy that sits between AI agents (Claude Code, Aider, LangChain, CrewAI, Continue.dev, etc.) and LLM inference providers (OpenAI, Anthropic, Ollama). Every intercepted interaction is logged to SQLite with full request/response bodies, reconstructed streaming text, token usage, cost estimates, and timing. A web UI and a CLI expose that data for inspection.

```
Agent (Claude Code, Aider, etc.)
  |  HTTP (with provider-specific base URL pointed at proxy)
  v
Interceptor Proxy  (localhost:8080)
  |  forward
  v
Real Provider (api.openai.com, api.anthropic.com, localhost:11434)
  |  response (possibly SSE/NDJSON stream)
  v
Proxy intercepts stream chunk-by-chunk, reconstructs, persists to SQLite
  |
  v
UI + CLI + admin API read from SQLite
```

Users redirect their agent with a single env var: `ANTHROPIC_BASE_URL=http://localhost:8080/_session/my-agent` (or the OpenAI/Ollama equivalent). The proxy strips the `_session/{id}` prefix, forwards upstream, and records everything keyed by that session id.

### Why a URL-prefix session id

Every request must carry a session id so multi-agent runs don't interleave. Two ways to provide it — URL prefix (`/_session/{id}/`) or header (`X-Interceptor-Conversation-Id`). Without one, the proxy returns a fake "please set a session id" LLM response and does **not** call upstream — fails safe, helps debugging.

### Components 

| Component | Location | Role |
|---|---|---|
| Starlette app + lifecycle | `src/agent_interception/proxy/server.py` | HTTP routes, admin API, catch-all proxy |
| Proxy handler | `src/agent_interception/proxy/handler.py` | receive → detect provider → forward → intercept → persist |
| Streaming interceptor | `src/agent_interception/proxy/streaming.py` | SSE and NDJSON stream pass-through with per-chunk capture |
| Provider parsers | `src/agent_interception/providers/{openai,anthropic,ollama}.py` | turn raw provider-specific JSON into a canonical `Interaction` |
| Registry | `src/agent_interception/providers/registry.py` | map path + headers → provider |
| Async SQLite store | `src/agent_interception/storage/store.py` | CRUD + aggregations (sessions, conversations, multi-agent graphs) |
| Migrations | `src/agent_interception/storage/migrations.py` | schema versioning (now at v5 after our addition) |
| CLI | `src/agent_interception/cli.py` | `start`, `replay`, `export`, `stats`, `sessions`, `save`, `conversations`, `visualize` |
| Terminal display | `src/agent_interception/display/terminal.py` | Rich live dashboard |
| Plotly charts | `src/agent_interception/display/charts.py` | 6-chart HTML report, optional PNG/SVG via kaleido |
| Demo scripts | `scripts/*.py` | Exercise the proxy via the Claude Agent SDK (code review, parallel analysis, multi-turn refactor, etc.) |

### What it captures per interaction

- **Request**: timestamp, method, path, headers (API keys redacted), full body, provider, model, system prompt, messages, tool definitions, image metadata
- **Response**: status, headers, full body, reconstructed text (even from streams), tool calls, individual stream chunks with timestamps
- **Derived**: token usage (input / output / total), cost estimate (per-provider pricing table), time-to-first-token, total latency, errors

### SQLite schema 

- `interactions` — one row per HTTP call. Columns include `session_id`, `conversation_id` (for multi-turn threading), `parent_interaction_id`, `turn_number`, `turn_type`, `agent_role` (for multi-agent labeling), `context_metrics`, full request/response blobs.
- `schema_version` — migrations are incremental; versions 1→5 documented in `migrations.py`.

### The UI 

- **ConversationHeader** / **AgentFlowGraph** — multi-agent interaction graph rendered with `reactflow` + `dagre`
- **TimelineView** — per-turn timeline with tool calls and token bars
- **DetailPanel** — split-pane: left list of turns, right detail with Summary / Request/Response / Messages / Tools / Raw tabs
- **InteractionsTable** — fallback raw-log table
- **Live feed** — SSE stream at `/_interceptor/live`; new interactions appear instantly in the graph
- **Theme switcher** — dark/light via `data-theme` attribute + Tailwind CSS variables

### Admin API 

Under `/_interceptor/*` for raw interaction access; under `/api/*` for UI-friendly shapes. Our analytics work added a new family at `/api/analytics/*` + the `/api/spans` ingest endpoint (see next section).

### Limitations 

- Agents with hardcoded endpoints that ignore base URL env vars can't be intercepted
- WebSocket (e.g. MCP servers) is out of scope — this is HTTP-only
- OpenAI's hidden reasoning tokens for o1/o3 aren't observable because the provider hides them
- Internal agent state/memory that doesn't hit the API is invisible (**this is precisely why Path B / the forwarded-span ingestion we added is the load-bearing thesis feature** — agents explicitly emit spans to cover what the proxy cannot see)

---

## How the thesis work connects

### What the proxy can see on its own (Path A — pre-existing)

The proxy sits on the network between the agent and the LLM provider. It only sees **HTTP requests that cross that boundary**.

Concrete example — one query through our Py benchmark:

```
agent process                                    proxy sees
─────────────────────────────────────────────    ──────────────────────
"What is Orientalism?"  (user question)
       │
       ▼
client.messages.create(tools=[...])    ───────►  HTTP POST /v1/messages
       ◄─── tool_use: web_search("...")          LLM response body
       │
       ▼
web_search("Orientalism")                        ▄▄▄ INVISIBLE ▄▄▄
  → Google CSE / DDG HTTP call                   (not to LLM provider,
  → returns list of URLs                          so proxy doesn't see it)
       │
       ▼
fetch_url(url_1)                                 ▄▄▄ INVISIBLE ▄▄▄
  → httpx GET britannica.com                     (not to LLM provider)
  → readability extraction
       │
       ▼
lexrank_summarize(text)                          ▄▄▄ INVISIBLE ▄▄▄
  → pure-Python PageRank                          (no HTTP at all)
       │
       ▼
client.messages.create(tool_result=...)  ──────► HTTP POST /v1/messages
       ◄─── final answer                         LLM response body
```

What the proxy records: **two HTTP calls**. Total wall time of those two calls. Tokens. Cost. That's it.

What happened between them — the 2 seconds fetching a web page, the 300ms of LexRank, any retries — is a black hole. The proxy sees "there was a gap of 2.3 seconds between LLM call 1 and LLM call 2" and that's all.

### Why this matters for the thesis

The thesis is about **tool-centric latency**. The whole Raj et al. argument is that tool execution (fetch, summarize) dominates the cost. But those are exactly the operations the proxy is blind to, because they don't hit the LLM provider.

So the proxy alone cannot answer "how much time did LexRank take?" or "did the fetch retry?" — the questions the thesis is literally about.

### What Path B adds

Path B lets the agent **self-report** its internal timeline. The agent's own code emits spans for every step and POSTs them to the interceptor:

```
agent process                                    proxy sees (via Path B)
─────────────────────────────────────────────    ──────────────────────
"What is Orientalism?"
       │
       ▼
with obs.root("agent.query"):                    ─────► root span
  with obs.span("llm.generate"):                 ─────► llm.generate span
    client.messages.create(...)                         (tokens, 943ms)
  with obs.span("tool.search"):                  ─────► tool.search span
    web_search("Orientalism")                           (0.1ms, 4 results)
  with obs.span("tool.fetch"):                   ─────► tool.fetch span
    fetch_url(u)                                        (820ms, 4566 bytes)
  with obs.span("tool.summarize"):               ─────► tool.summarize span
    lexrank(text)                                       (if called)
  with obs.span("llm.generate"):                 ─────► llm.generate span
    client.messages.create(...)                         (tokens, 1250ms)
# on root close → POST /api/spans
```

Now the extended proxy (new `POST /api/spans` endpoint, new `ingested_spans` table, new Analytics UI tab) sees the **full timeline**, not just the network calls.

### Two complementary paths into the same system

Path B is not a separate observability system. It shares the same process, SQLite file, and web UI as the base proxy:

| | Path A (pre-existing) | Path B (thesis addition) |
|---|---|---|
| How the data gets in | Proxy intercepts HTTP to LLM provider | Agent emits spans, POSTs to `/api/spans` |
| Where it's stored | `interactions` table | `ingested_spans` + `ingested_traces` tables |
| What it captures | LLM calls only | Every instrumented step (tools, retries, reasoning, anything) |
| Which UI tab | Workspace | Analytics |
| Agent cooperation needed? | No (just redirect base URL) | Yes (import obs library + call `forward_to=...`) |

### Why "completes" rather than "parallel"

If Path B were a separate observability system — different DB, different UI, different process — it would be parallel. You'd have to look at two places to understand a run.

Instead, we wired it into the existing proxy: same SQLite file (with new tables alongside), same server process (with new routes), same web UI (with a new tab reusing the existing header/theme/components). A single view of a session shows both halves together: the HTTP calls (Path A) correlated with the agent-internal steps (Path B). The blind spots in the network view are filled in by the self-reported spans.

That is the "completes" part: one proxy project that now observes both sides of the agent–network boundary, instead of just the network side. Any third-party agent can produce Path B traces by importing `benchmark.obs.Observer` (Python) or `benchmark-go/internal/obs` (Go) and setting `forward_to=http://localhost:8080/api/spans`; our benchmark is the reference producer but nothing in the contract is benchmark-specific.

---

## Architecture (thesis additions on top of the base)

Three layers, three directories, one shared span schema:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: Instrumentation (the "obs" library)                       │
│    benchmark/obs.py           (Python, ~220 LoC)                    │
│    benchmark-go/internal/obs/ (Go,     ~240 LoC, zero external deps)│
│                                                                     │
│    Produces JSON with identical shape across languages.             │
│    Enforced by 7 pytest assertions against a fixed-clock golden.    │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ (write to disk AND forward_to=URL)
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2: Benchmark (the thesis workload)                           │
│    benchmark/configs/config_py.py     — Py + Claude native tools    │
│    benchmark-go/internal/agent/       — Go + Claude native tools    │
│    benchmark/tools/*                  — Python search/fetch/sumy    │
│    benchmark-go/internal/tools/*      — Go search/fetch/LexRank port│
│    benchmark/queries/freshqa_20.json  — 20 FreshQA queries (seed=42)│
│                                                                     │
│    Both configs: Claude Haiku 4.5 + same tool_specs, 20 same queries│
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ POST /api/spans
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3: Live analytics (built on the existing agent-interceptor)  │
│    src/agent_interception/storage/spans_store.py   — SQLite         │
│    src/agent_interception/proxy/server.py          — 4 new routes   │
│    frontend/src/pages/AnalyticsPage.tsx            — new UI tab     │
│    frontend/src/components/analytics/*             — 4 components   │
│                                                                     │
│    "Per session" view: click a session, see stacked cost breakdown. │
│    "All sessions" view: grouped bars per config (benchmark plot_2). │
└─────────────────────────────────────────────────────────────────────┘
```

Any agent — not just our benchmark — can push traces into Layer 3 by importing the obs module and setting `forward_to="http://<interceptor>/api/spans"`.

---

## Span schema (the contract)

One top-level JSON document per trace:
```
{ "trace_id", "config", "query_id", "label", "spans": [ ... ] }
```

Span kinds:
- `root` — exactly one per trace, named `agent.query`
- `tool` — `tool.search`, `tool.fetch`, `tool.summarize` (canonical); any `tool.*` also honored
- `llm` — `llm.generate` (one per HTTP attempt; sets `llm.attempt`, `llm.rate_limited`, token counts, `stop_reason`)
- `internal` — `llm.retry_wait` (sibling of `llm.generate`, measures 429 backoff sleep with `llm.retry_after_s`)

Every span carries: `name`, `trace_id`, `span_id`, `parent_id`, `start_ns`, `end_ns`, `wall_time_ms`, `cpu_time_ms`, `kind`, `attrs`, `status`, `error`.

Derived per-session metrics (computed at read time, not write time):
- `active_latency_ms` = root wall − inter-turn pauses − retry waits (real work time)
- `tool_{search,fetch,summarize}_ms`, `tool_time_ms`
- `llm_time_ms` (sum of successful `llm.generate` attempts only)
- `retry_wait_ms`, `num_retry_waits`
- `num_tool_calls`, `num_llm_turns`, `input_tokens_total`, `output_tokens_total`

---

## What we built in order

1. **FreshQA subset** (20 queries, seed=42, stratified 1hop/multihop × never/slow/fast-changing) — committed as JSON.
2. **Python obs layer** — context-manager API over the OpenTelemetry SDK, writes one JSON per trace.
3. **Python tools** — `httpx` + trafilatura for fetch; `sumy` for LexRank; static-URL + DDG + Google CSE search backends.
4. **Config Py** — Claude tool-use loop using the anthropic SDK.
5. **Analysis scripts** — `metrics.py` (CSV), `plots.py` (plot_2 stacked bars, plot_3 signals histograms, plot_4 ratio).
6. **Cross-language schema test** — regenerates a golden Python trace with fixed clocks, builds & runs the Go equivalent, diffs the two JSON docs (7/7 assertions).
7. **Go obs layer** — same JSON shape, pure stdlib.
8. **Go LexRank port** — ~170 LoC hand-port of Erkan & Radev 2004, 5/5 unit tests.
9. **Go tools** — `net/http` + regex extractor; same 3 search backends.
10. **Go Claude agent** — raw `net/http` POST to `/v1/messages`, zero-dep SDK, byte-for-byte control over tool_spec serialization.
11. **20-query sweep v1** — all three traces per config, 40 JSON files.
12. **Live analytics backend** — SQLite migration v5 (`ingested_spans`, `ingested_traces` tables), `POST /api/spans`, `GET /api/analytics/*` endpoints, 7/7 integration tests.
13. **obs `forward_to=URL`** — auto-POST each finished trace to a live endpoint. Local JSON remains authoritative.
14. **Analytics UI** — new tab in the existing frontend (Vite + React + Tailwind). "Per session" and "All sessions" views.
15. **Retry-span refactor (v2)** — each HTTP attempt gets its own `llm.generate` span; backoff sleeps become `llm.retry_wait` siblings. Revealed that v1's 1.43× Go-slow-on-LLM was a measurement artifact.
16. **20-query sweep v2** — re-run with the new schema; comparison clean.

---

## Key findings

### On the thesis claim
- **Span schema is portable** — one `metrics.py` reads both Python-emitted and Go-emitted traces.
- **Cross-language comparison is legible** only because the spans are semantic, not bytes of HTTP.
- **Framework fixes its own measurement**: v2 retry-split changed the LLM ratio from a bogus 1.43× to an honest 1.01× without touching any agent code. That's the thesis in one bullet.

### On the workload (Claude Haiku 4.5 + native tools, v2 clean 13-query comparison)

| stage | Py mean | Go mean | go/py | reading |
|---|---:|---:|---:|---|
| tool.search | 0.26 ms | 0.02 ms | 0.07× | noise |
| tool.fetch | 760 ms | 1003 ms | 1.32× | Go slower this run; dominated by one slow Britannica response |
| tool.summarize | — | — | — | **Claude never called it in 33 completed runs** |
| llm.generate | 3925 ms | 3961 ms | **1.01×** | tied |
| llm.retry_wait | 0 ms | 616 ms | — | Go had 2×429, Py had 0 |
| LLM turns | 3.00 | 2.85 | — | identical decision-making |
| tool calls | 2.38 | 2.15 | — | identical decision-making |

### On the paper (Raj et al. Figure 2c)
Their LangChain + vLLM + gpt-oss-20b workload shows tools dominating (~55% of E2E). Our Haiku + native tools shows LLM dominating (~88-90%). The cost-breakdown shift is a property of the LLM/tool-interface combination, not an invariant of agentic AI. **Our framework makes this visible in one side-by-side plot.**

---

## What's tested (all green)

| test file | count | what |
|---|---:|---|
| `tests/test_cross_lang/test_schema_equivalence.py` | 7 | Py and Go emit byte-equivalent JSON given the same fixed clock |
| `tests/test_proxy/test_analytics_ingest.py` | 7 | `POST /api/spans` round-trip; full `obs.py → HTTP → list_sessions` e2e |
| `benchmark-go/internal/tools/summarize_test.go` | 5 | LexRank port: top-1 correctness, document order, empty handling |
| Existing proxy test suite | 152 | unchanged, all pass after our changes |
| **total** | **171** | |

Plus one E2E smoke (live server + 40 trace POSTs + bulk endpoint verification) run manually, green.

---

Last updated 2026-04-23. Session-by-session notes live in the conversation transcript; committed history (once we commit) will be the authoritative record.
