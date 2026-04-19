# Multi-Agent Interaction Capture — What Was Built

## Overview

This document explains the multi-agent features added to `agent-interception` in the `multi_Agent_graph` branch. The proxy already intercepted LLM calls and stored them as `Interaction` records in SQLite. What was missing was first-class support for tracking **which agent made which call**, and **how agents hand off work to each other**.

---

## What Is `agent-interception`?

`agent-interception` is an HTTP proxy that sits between your AI agents and the LLM provider (Anthropic, OpenAI, Ollama). Every LLM API call is intercepted, parsed, and stored in a local SQLite database. Agents route their calls through session-prefixed URLs:

```
http://proxy:8080/_session/{agent-id}/v1/messages
```

The `{agent-id}` becomes the `session_id` that groups all calls from one agent together. The proxy also tracks conversation threading — linking sequential turns into conversation chains using `conversation_id`, `parent_interaction_id`, and `turn_number`.

---

## What Was Added

### 1. `agent_role` Field on Every Interaction

**File changed:** `src/agent_interception/models.py`

A new optional field was added to the `Interaction` model:

```python
agent_role: str | None = Field(
    default=None, description="Agent role: orchestrator | subagent | tool"
)
```

**Purpose:** In a multi-agent system, not all agents are equal. An orchestrator agent coordinates work, subagents execute specific tasks, and tool agents wrap external APIs. This field lets you label each session by its role in the system.

**Valid values:**
- `"orchestrator"` — the top-level agent that plans and delegates
- `"subagent"` — a worker agent that receives tasks from the orchestrator
- `"tool"` — an agent that wraps a specific tool or external service

**Default:** `None` — existing interactions are completely unaffected.

---

### 2. Database Migration (v4)

**File changed:** `src/agent_interception/storage/migrations.py`

A new migration adds the `agent_role` column to the `interactions` table in SQLite:

```sql
ALTER TABLE interactions ADD COLUMN agent_role TEXT;
CREATE INDEX IF NOT EXISTS idx_interactions_agent_role ON interactions(agent_role);
```

The index makes it efficient to filter or group interactions by role. The migration runs automatically on startup — no manual action needed. The schema version counter moved from `3` → `4`.

---

### 3. `X-Agent-Role` Header Extraction

**File changed:** `src/agent_interception/proxy/handler.py`

When an agent makes an LLM call, it can now include an `X-Agent-Role` header:

```
X-Agent-Role: orchestrator
```

The proxy handler reads this header and stores it on the interaction before saving to the database. Only the three valid values are accepted; any other value is ignored (stored as `None`).

**How an agent sets its role in practice:**

```python
import httpx

response = httpx.post(
    "http://proxy:8080/_session/my-orchestrator/v1/messages",
    headers={
        "X-Agent-Role": "orchestrator",
        "Authorization": "Bearer sk-...",
    },
    json={"model": "claude-opus-4-6", "messages": [...]}
)
```

---

### 4. Three New Graph Models

**File changed:** `src/agent_interception/models.py`

Three Pydantic models were added to represent the multi-agent graph:

```python
class AgentNode(BaseModel):
    session_id: str          # identifies the agent
    agent_role: str | None   # orchestrator | subagent | tool | None
    interaction_count: int   # how many LLM calls this agent made
    total_tokens: int        # sum of input + output tokens across all calls
    total_cost_usd: float    # total estimated cost in USD

class AgentEdge(BaseModel):
    from_session_id: str     # agent that handed off work
    to_session_id: str       # agent that received work
    interaction_id: str      # the specific interaction where the handoff happened
    turn_number: int         # turn index in the conversation at handoff time
    latency_ms: float | None # latency of the handoff interaction

class AgentGraph(BaseModel):
    conversation_id: str
    nodes: list[AgentNode]
    edges: list[AgentEdge]
```

**What this represents:** A directed graph where:
- Each **node** is one agent session (identified by `session_id`)
- Each **edge** is a handoff — a moment when one agent finished and another agent picked up the same conversation thread

---

### 5. `get_agent_graph()` Store Method

**File changed:** `src/agent_interception/storage/store.py`

A new async method builds the graph from stored interactions:

```python
async def get_agent_graph(self, conversation_id: str) -> AgentGraph
```

**How it works:**

1. **Fetch all interactions** for the given `conversation_id`, ordered by turn number.
2. **Build nodes** — group interactions by `session_id`, summing token counts and costs.
3. **Build edges** — for every interaction where `turn_type = "handoff"`, look up the parent interaction (via `parent_interaction_id`) to find which agent handed off to which.
4. Return an `AgentGraph`.

**Handoff detection** relies on the existing conversation threading logic. When a new interaction arrives with the same `conversation_id` but a different `session_id` than the previous turn, the threading algorithm automatically sets `turn_type = "handoff"` and links it to the parent interaction. `get_agent_graph()` reads these pre-computed handoff markers.

**Edge case:** If `conversation_id` has no interactions, returns an empty `AgentGraph` (no exception).

---

### 6. New API Endpoint

**File changed:** `src/agent_interception/proxy/server.py`

```
GET /api/conversations/{conversation_id}/agent-graph
```

**Responses:**
- `200 OK` — returns the `AgentGraph` as JSON
- `404 Not Found` — if the conversation has no interactions

**Example response:**

```json
{
  "conversation_id": "abc-123",
  "nodes": [
    {
      "session_id": "orchestrator-agent",
      "agent_role": "orchestrator",
      "interaction_count": 3,
      "total_tokens": 4200,
      "total_cost_usd": 0.021
    },
    {
      "session_id": "search-subagent",
      "agent_role": "subagent",
      "interaction_count": 1,
      "total_tokens": 800,
      "total_cost_usd": 0.004
    }
  ],
  "edges": [
    {
      "from_session_id": "orchestrator-agent",
      "to_session_id": "search-subagent",
      "interaction_id": "550e8400-e29b-41d4-a716-446655440000",
      "turn_number": 2,
      "latency_ms": 312.5
    }
  ]
}
```

---

## End-to-End Pipeline

Here is the full flow from agent call to graph query:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Process                                                       │
│                                                                      │
│  orchestrator sends:                                                 │
│    POST http://proxy:8080/_session/orchestrator/v1/messages          │
│    X-Agent-Role: orchestrator                                        │
│    X-Interceptor-Conversation-Id: conv-abc                           │
│                                                                      │
│  subagent sends:                                                     │
│    POST http://proxy:8080/_session/search-agent/v1/messages          │
│    X-Agent-Role: subagent                                            │
│    X-Interceptor-Conversation-Id: conv-abc   ← same conversation ID  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Proxy Handler  (handler.py)                                         │
│                                                                      │
│  1. Extract session_id from URL path prefix                          │
│  2. Extract agent_role from X-Agent-Role header                      │
│  3. Extract conversation_id from X-Interceptor-Conversation-Id       │
│  4. Build Interaction object with all fields                         │
│  5. Forward request to upstream LLM provider                         │
│  6. Collect response (streaming or non-streaming)                    │
│  7. Call store.save(interaction)                                     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  InteractionStore.save()  (store.py)                                 │
│                                                                      │
│  _resolve_threading():                                               │
│    - Finds previous turn in the same conversation_id                 │
│    - If prev.session_id ≠ current.session_id                        │
│        → turn_type = "handoff"                                       │
│        → parent_interaction_id = prev.id                             │
│    - Otherwise: "continuation" or "tool_result"                      │
│                                                                      │
│  INSERT INTO interactions (..., agent_role) VALUES (...)             │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SQLite Database                                                     │
│                                                                      │
│  interactions table (v4 schema):                                     │
│    id, session_id, conversation_id, turn_number, turn_type,          │
│    parent_interaction_id, agent_role, token_usage, cost_estimate,    │
│    ... (all other fields)                                            │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  GET /api/conversations/{id}/agent-graph                             │
│                                                                      │
│  store.get_agent_graph(conversation_id):                             │
│    1. Load all interactions for this conversation                    │
│    2. Group by session_id → AgentNode list                          │
│    3. For each turn_type="handoff":                                  │
│         look up parent → create AgentEdge                           │
│    4. Return AgentGraph { nodes, edges }                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## How Handoffs Are Detected (Key Mechanism)

The handoff detection is entirely automatic. You only need to pass the **same `conversation_id`** across agents. The proxy does the rest:

```
Turn 1: session=orchestrator, conversation=conv-abc → turn_type="initial"
Turn 2: session=orchestrator, conversation=conv-abc → turn_type="continuation"
Turn 3: session=search-agent, conversation=conv-abc → turn_type="handoff" ← detected!
Turn 4: session=search-agent, conversation=conv-abc → turn_type="continuation"
Turn 5: session=orchestrator, conversation=conv-abc → turn_type="handoff" ← detected again!
```

Each `handoff` turn becomes an edge in the graph from the previous session to the current one.

---

## Files Changed

| File | What Changed |
|------|-------------|
| `src/agent_interception/models.py` | Added `agent_role` field to `Interaction`; added `AgentNode`, `AgentEdge`, `AgentGraph` models |
| `src/agent_interception/storage/migrations.py` | Added v4 migration: `agent_role` column + index |
| `src/agent_interception/proxy/handler.py` | Reads `X-Agent-Role` header and sets `agent_role` on the interaction |
| `src/agent_interception/storage/store.py` | Added `agent_role` to `save()` and `_row_to_interaction()`; added `get_agent_graph()` method |
| `src/agent_interception/proxy/server.py` | Added `GET /api/conversations/{id}/agent-graph` route |
| `tests/test_storage/test_store.py` | Added 3 tests: single agent graph, handoff graph, not found |
| `tests/test_proxy/test_handler.py` | Added 1 test: agent role header extraction |

---

## Tests Added

### `tests/test_storage/test_store.py`

| Test | What It Verifies |
|------|-----------------|
| `test_agent_graph_single_agent` | Two interactions with same `session_id` → 1 node, 0 edges |
| `test_agent_graph_handoff` | Two interactions with different `session_id`, same `conversation_id` → 2 nodes, 1 edge with correct from/to |
| `test_agent_graph_not_found` | Unknown `conversation_id` → empty `AgentGraph`, no exception |

### `tests/test_proxy/test_handler.py`

| Test | What It Verifies |
|------|-----------------|
| `test_agent_role_extracted_from_header` | Request with `X-Agent-Role: orchestrator` → saved interaction has `agent_role = "orchestrator"` |

---

## Backwards Compatibility

- `agent_role` defaults to `None` — all existing interactions are unaffected
- The v4 migration uses `ALTER TABLE ... ADD COLUMN` — existing databases are upgraded automatically without data loss
- All 136 pre-existing tests continue to pass; 4 new tests were added (140 total)
