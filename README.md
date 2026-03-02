# Agent Interception

A transparent HTTP reverse proxy that sits between AI agents (Claude Code, Aider, LangChain, CrewAI, etc.) and LLM inference providers (OpenAI, Anthropic, Ollama), logging every interaction in full detail.

Agents connect by setting a single environment variable (e.g., `ANTHROPIC_BASE_URL=http://localhost:8080`). The proxy detects the provider from the request path, forwards to the real upstream, intercepts the response (including SSE streams), and logs everything to SQLite.

```
Agent (Claude Code, Aider, etc.)
  |  HTTP
  v
Interceptor Proxy (localhost:8080)
  |
  v
Real Provider (api.openai.com, api.anthropic.com, localhost:11434)
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Installation

```bash
# Clone and install dependencies
cd agent_interception
uv sync --dev
```

## Quick Start

### 1. Start the proxy

```bash
uv run agent-interceptor start
```

This starts the proxy on `http://127.0.0.1:8080` with default settings. The SQLite database is created at `interceptor.db`.

### 2. Point your agent at the proxy

Set the appropriate environment variable for your agent:

| Agent | Env Var | Value |
|---|---|---|
| Claude Code | `ANTHROPIC_BASE_URL` | `http://localhost:8080` |
| Aider | `OPENAI_API_BASE` | `http://localhost:8080/v1` |
| Continue.dev | `apiBase` in config | `http://localhost:8080/v1` |
| LangChain (OpenAI) | `OPENAI_BASE_URL` | `http://localhost:8080/v1` |
| LangChain (Anthropic) | `ANTHROPIC_BASE_URL` | `http://localhost:8080` |
| CrewAI | `OPENAI_BASE_URL` | `http://localhost:8080/v1` |
| Ollama clients | `OLLAMA_HOST` | `http://localhost:8080` |
| OpenAI SDK | `OPENAI_BASE_URL` | `http://localhost:8080/v1` |
| Anthropic SDK | `ANTHROPIC_BASE_URL` | `http://localhost:8080` |

Example with Claude Code:

```bash
ANTHROPIC_BASE_URL=http://localhost:8080 claude -p "Hello"
```

### 3. View captured interactions

```bash
# Show recent interactions in the terminal
uv run agent-interceptor replay --last 10

# Show aggregate stats
uv run agent-interceptor stats

# Generate an interactive HTML dashboard
uv run agent-interceptor visualize

# Export to JSON
uv run agent-interceptor export -o log.json

# Export as JSONL
uv run agent-interceptor export --format jsonl -o log.jsonl
```

## CLI Reference

### `agent-interceptor start`

Start the proxy server.

```
Options:
  --host TEXT          Host to bind to (default: 127.0.0.1)
  --port INTEGER       Port to bind to (default: 8080)
  --db TEXT             Path to SQLite database (default: interceptor.db)
  --openai-url TEXT     OpenAI upstream URL (default: https://api.openai.com)
  --anthropic-url TEXT  Anthropic upstream URL (default: https://api.anthropic.com)
  --ollama-url TEXT     Ollama upstream URL (default: http://localhost:11434)
  -v, --verbose        Verbose output (show full request/response bodies)
  -q, --quiet          Suppress terminal output
  --no-redact          Disable API key redaction in logs
  --no-store-chunks    Don't store individual stream chunks (saves disk space)
```

All options can also be set via environment variables with the `INTERCEPTOR_` prefix:

```bash
INTERCEPTOR_PORT=9090 INTERCEPTOR_DB_PATH=my.db uv run agent-interceptor start
```

### `agent-interceptor replay`

Replay recent interactions from the database.

```
Options:
  --db TEXT         Database path
  --last INTEGER   Number of recent interactions (default: 10)
  --provider TEXT   Filter by provider (openai, anthropic, ollama)
  --model TEXT      Filter by model name
  -v, --verbose    Show full details
```

### `agent-interceptor export`

Export interactions as JSON or JSONL.

```
Options:
  --db TEXT           Database path
  --last INTEGER     Number of interactions (default: 50)
  --provider TEXT     Filter by provider
  --model TEXT        Filter by model
  -o, --output TEXT   Output file (default: stdout)
  --format [json|jsonl]  Output format (default: json)
```

### `agent-interceptor stats`

Show aggregate statistics.

```
Options:
  --db TEXT   Database path
```

### `agent-interceptor sessions`

List all captured sessions.

```
Options:
  --db TEXT   Database path
```

### `agent-interceptor conversations`

List all tracked conversation threads with per-thread turn counts and token totals.

```
Options:
  --db TEXT   Database path
```

### `agent-interceptor visualize`

Generate an interactive HTML dashboard or static image charts from captured interactions.

```
Options:
  --db TEXT                  Database path
  -o, --output TEXT          Output path (default: report.html for html; ./charts for png/svg)
  -f, --format [html|png|svg] Output format (default: html)
  --last INTEGER             Limit to last N interactions (default: 200)
  --provider TEXT            Filter by provider
  --model TEXT               Filter by model
  --session TEXT             Filter by session ID
```

The HTML report loads Plotly.js from CDN and requires an internet connection to view. It contains six charts:

- **Latency Over Time** — total latency and TTFT scatter, colored by provider
- **Token Usage** — input/output tokens per interaction as a grouped bar chart
- **Cumulative Cost** — running USD cost as a filled line chart
- **Provider Distribution** — pie or bar chart of request counts per provider
- **Context Window Growth** — context depth over conversation turns, one trace per thread
- **Latency Distribution** — histogram with mean marker

Examples:

```bash
# Interactive HTML dashboard (default)
uv run agent-interceptor visualize
uv run agent-interceptor visualize -o my_report.html
uv run agent-interceptor visualize --last 50 --provider anthropic
uv run agent-interceptor visualize --session agent-a

# Static PNG/SVG export (requires kaleido)
uv sync --group viz-static
uv run agent-interceptor visualize -f png            # → ./charts/
uv run agent-interceptor visualize -f svg -o /tmp/charts
```

### `agent-interceptor save <session-id>`

Export a session's interactions to a file.

```
Options:
  --db TEXT             Database path
  -o, --output TEXT     Output file (default: <session-id>.json)
  --format [json|jsonl] Output format (default: json)
```

## Admin API

While the proxy is running, these endpoints are available:

| Endpoint | Method | Description |
|---|---|---|
| `/_interceptor/health` | GET | Health check |
| `/_interceptor/stats` | GET | Aggregate statistics |
| `/_interceptor/sessions` | GET | List all sessions |
| `/_interceptor/interactions` | GET | List interactions (`?limit=`, `?offset=`, `?provider=`, `?model=`, `?session_id=`) |
| `/_interceptor/interactions/{id}` | GET | Full interaction detail |
| `/_interceptor/interactions` | DELETE | Clear all interactions |

Examples:

```bash
# Health check
curl http://localhost:8080/_interceptor/health

# Stats
curl http://localhost:8080/_interceptor/stats

# List recent 5 interactions
curl "http://localhost:8080/_interceptor/interactions?limit=5"

# Get full detail for one interaction
curl http://localhost:8080/_interceptor/interactions/<id>

# Clear all interactions
curl -X DELETE http://localhost:8080/_interceptor/interactions
```

## Session Tracking

When multiple agents run simultaneously, their API calls are interleaved. To separate them, use **session-tagged URLs** — encode a session ID in the base URL path:

```bash
# Instead of this:
ANTHROPIC_BASE_URL=http://localhost:8080

# Use this:
ANTHROPIC_BASE_URL=http://localhost:8080/_session/my-session-name
```

The proxy strips the `/_session/{id}` prefix, records the session ID on every interaction, then routes normally. Each agent gets its own session ID, and you can list and export sessions independently.

### CLI Session Commands

```bash
# List all sessions
uv run agent-interceptor sessions

# Export a session to a file
uv run agent-interceptor save <session-id>
uv run agent-interceptor save <session-id> -o custom-name.json
uv run agent-interceptor save <session-id> --format jsonl
```

### Example: Two agents at once

```bash
# Terminal 1: Agent A
ANTHROPIC_BASE_URL=http://localhost:8080/_session/agent-a claude -p "Review code"

# Terminal 2: Agent B
ANTHROPIC_BASE_URL=http://localhost:8080/_session/agent-b claude -p "Write tests"

# See them separated
uv run agent-interceptor sessions
# SESSION ID       COUNT  MODELS                STARTED
# agent-a              7  claude-opus-4-6       2026-02-10T...
# agent-b              5  claude-opus-4-6       2026-02-10T...

# Export just one
uv run agent-interceptor save agent-a
```

The demo scripts automatically generate unique session IDs (e.g., `code-review-a1b2c3d4`).

### Admin API

Sessions are also available via the HTTP API:

```bash
# List all sessions
curl http://localhost:8080/_interceptor/sessions

# Filter interactions by session
curl "http://localhost:8080/_interceptor/interactions?session_id=agent-a"
```

## What Gets Logged

Per interaction:

- **Request**: timestamp, method, path, headers (API keys redacted), full body, provider, model, system prompt, messages, tool definitions
- **Response**: status code, headers, full body, reconstructed text (from stream if streaming), tool calls, individual stream chunks with timestamps
- **Metadata**: token usage, cost estimate, time-to-first-token, total latency, errors, image metadata (count/type/size, not raw base64)

## Demo Scripts

The `scripts/` directory contains demo scripts that exercise the proxy using the [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-agent-sdk). These require the `claude-agent-sdk` Python package and a working `claude` CLI.

Install the SDK first:

```bash
uv add claude-agent-sdk
```

Start the proxy in one terminal, then run scripts in another:

```bash
# Terminal 1: Start the proxy
uv run agent-interceptor start --verbose

# Terminal 2: Run a demo
uv run python scripts/code_review.py
```

### Available Scripts

| Script | Description | Turns | Tools Exercised |
|---|---|---|---|
| `code_review.py` | Deep code review of the project | 1 | Read, Glob, Grep |
| `generate_report.py` | Run tests and write a project report | 1 | Bash, Write, Read, Glob |
| `parallel_analysis.py` | 3 parallel sub-agents analyze different modules | 1 | Read, Glob, Grep, Task |
| `multi_turn_refactor.py` | 3-turn refactoring session with session resume | 3 | Read, Write, Glob, Grep, Bash |
| `design_discussion.py` | 3-turn architecture brainstorm (read-only) | 3 | Read, Glob, Grep |
| `verify_logs.py` | Validates captured interactions and exports to JSON | - | (uses admin API) |

### Script Configuration

Scripts use `_common.py` for shared setup. You can override defaults via env vars:

```bash
# Use a different proxy URL
INTERCEPTOR_URL=http://127.0.0.1:9090 uv run python scripts/code_review.py

# Use a specific claude CLI binary
CLAUDE_CLI_PATH=/usr/local/bin/claude uv run python scripts/code_review.py
```

### Verifying Results

After running one or more scripts, validate and export the logs:

```bash
uv run python scripts/verify_logs.py
```

This checks all entries for completeness, reports field coverage, and exports the full log to `interceptor_log.json`.

## Provider Detection

The proxy routes requests based on URL path:

| Path | Provider | Confirmed By |
|---|---|---|
| `/v1/messages` | Anthropic | `anthropic-version` header |
| `/v1/*` (anything else) | OpenAI | Default for `/v1/` paths |
| `/api/*` | Ollama | Path prefix |
| Unknown | Passthrough | Raw logging only |

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test modules
uv run pytest tests/test_providers/ -v
uv run pytest tests/test_storage/ -v
uv run pytest tests/test_proxy/ -v
uv run pytest tests/test_integration/ -v

# Lint and format
uv run ruff check --fix && uv run ruff format

# Type checking
uv run pyright
```

## Project Structure

```
agent_interception/
├── pyproject.toml
├── src/agent_interception/
│   ├── cli.py                   # Click CLI: start, replay, export, stats, sessions, save, conversations, visualize
│   ├── config.py                # InterceptorConfig (pydantic-settings)
│   ├── models.py                # Interaction, StreamChunk, TokenUsage, etc.
│   ├── proxy/
│   │   ├── server.py            # Starlette app + lifecycle + admin API
│   │   ├── handler.py           # Core: receive -> forward -> intercept -> log
│   │   └── streaming.py         # SSE/NDJSON stream interception
│   ├── providers/
│   │   ├── base.py              # Abstract ProviderParser interface
│   │   ├── openai.py            # OpenAI request/response/stream parsing
│   │   ├── anthropic.py         # Anthropic parsing (all SSE event types)
│   │   ├── ollama.py            # Ollama NDJSON parsing
│   │   └── registry.py          # Path + header -> Provider detection
│   ├── storage/
│   │   ├── store.py             # Async SQLite CRUD
│   │   └── migrations.py        # Schema DDL
│   └── display/
│       ├── terminal.py          # Rich real-time display
│       └── charts.py            # Plotly chart generation + HTML/image export
├── scripts/                     # Demo scripts (Claude Agent SDK)
└── tests/                       # pytest test suite
```

## Limitations

| Limitation | Why |
|---|---|
| Agents with hardcoded endpoints | Won't respect base URL env vars |
| WebSocket protocols (MCP servers) | HTTP-only proxy |
| OpenAI hidden reasoning tokens (o1/o3) | Provider hides them |
| Internal agent state/memory | Never hits the API |
| Certificate-pinned agents | Reject non-original certs |
