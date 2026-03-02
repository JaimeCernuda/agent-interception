"""Async SQLite store for intercepted interactions."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from agent_interception.config import InterceptorConfig
from agent_interception.models import (
    CostEstimate,
    ImageMetadata,
    Interaction,
    Provider,
    StreamChunk,
    TokenUsage,
)
from agent_interception.storage.migrations import apply_migrations


def _serialize_json(value: Any) -> str | None:
    """Serialize a value to JSON string, or None if value is None."""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(
            [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
        )
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"))
    return json.dumps(value)


def _deserialize_json(value: str | None) -> Any:
    """Deserialize a JSON string, or None if value is None."""
    if value is None:
        return None
    return json.loads(value)


class InteractionStore:
    """Async SQLite store for saving and querying interactions."""

    def __init__(self, config: InterceptorConfig) -> None:
        self._config = config
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database connection and apply migrations."""
        self._db = await aiosqlite.connect(self._config.db_path)
        self._db.row_factory = aiosqlite.Row
        await apply_migrations(self._db)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        """Get the database connection, raising if not initialized."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        return self._db

    async def save(self, interaction: Interaction) -> None:
        """Save an interaction to the database."""
        chunks_json = (
            _serialize_json(interaction.stream_chunks) if self._config.store_stream_chunks else None
        )
        await self.db.execute(
            """
            INSERT OR REPLACE INTO interactions (
                id, session_id, timestamp, method, path, request_headers, request_body,
                raw_request_body, provider, model, system_prompt, messages, tools,
                image_metadata, status_code, response_headers, response_body,
                raw_response_body, is_streaming, stream_chunks, response_text,
                tool_calls, token_usage, cost_estimate, time_to_first_token_ms,
                total_latency_ms, error
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                interaction.id,
                interaction.session_id,
                interaction.timestamp.isoformat(),
                interaction.method,
                interaction.path,
                json.dumps(interaction.request_headers),
                _serialize_json(interaction.request_body),
                interaction.raw_request_body,
                interaction.provider.value,
                interaction.model,
                interaction.system_prompt,
                _serialize_json(interaction.messages),
                _serialize_json(interaction.tools),
                _serialize_json(interaction.image_metadata),
                interaction.status_code,
                json.dumps(interaction.response_headers),
                _serialize_json(interaction.response_body),
                interaction.raw_response_body,
                int(interaction.is_streaming),
                chunks_json,
                interaction.response_text,
                _serialize_json(interaction.tool_calls),
                _serialize_json(interaction.token_usage),
                _serialize_json(interaction.cost_estimate),
                interaction.time_to_first_token_ms,
                interaction.total_latency_ms,
                interaction.error,
            ),
        )
        await self.db.commit()

    async def get(self, interaction_id: str) -> Interaction | None:
        """Get an interaction by ID."""
        cursor = await self.db.execute("SELECT * FROM interactions WHERE id = ?", (interaction_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_interaction(row)

    async def list_interactions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        provider: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> list[Interaction]:
        """List interactions with optional filtering."""
        query = "SELECT * FROM interactions"
        params: list[Any] = []
        conditions: list[str] = []

        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        if model:
            conditions.append("model = ?")
            params.append(model)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_interaction(row) for row in rows]

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with summary info.

        Includes a virtual ``__unsessioned__`` entry for interactions
        that were captured without a session prefix.
        """
        cursor = await self.db.execute(
            """
            SELECT
                session_id,
                COUNT(*) as interaction_count,
                MIN(timestamp) as first_interaction,
                MAX(timestamp) as last_interaction,
                GROUP_CONCAT(DISTINCT provider) as providers,
                GROUP_CONCAT(DISTINCT model) as models,
                SUM(total_latency_ms) as total_latency_ms
            FROM interactions
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            ORDER BY first_interaction DESC
            """
        )
        rows = await cursor.fetchall()
        sessions = [
            {
                "session_id": row[0],
                "interaction_count": row[1],
                "first_interaction": row[2],
                "last_interaction": row[3],
                "providers": row[4].split(",") if row[4] else [],
                "models": row[5].split(",") if row[5] else [],
                "total_latency_ms": row[6],
            }
            for row in rows
        ]

        # Include a virtual entry for interactions captured without a session prefix
        cursor = await self.db.execute(
            """
            SELECT
                COUNT(*) as interaction_count,
                MIN(timestamp) as first_interaction,
                MAX(timestamp) as last_interaction,
                GROUP_CONCAT(DISTINCT provider) as providers,
                GROUP_CONCAT(DISTINCT model) as models,
                SUM(total_latency_ms) as total_latency_ms
            FROM interactions
            WHERE session_id IS NULL
            """
        )
        row = await cursor.fetchone()
        if row and row[0] and row[0] > 0:
            sessions.append({
                "session_id": "__unsessioned__",
                "interaction_count": row[0],
                "first_interaction": row[1],
                "last_interaction": row[2],
                "providers": row[3].split(",") if row[3] else [],
                "models": row[4].split(",") if row[4] else [],
                "total_latency_ms": row[5],
            })

        return sessions

    async def clear(self) -> int:
        """Delete all interactions. Returns the number of rows deleted."""
        cursor = await self.db.execute("SELECT COUNT(*) FROM interactions")
        count_row = await cursor.fetchone()
        count = count_row[0] if count_row else 0
        await self.db.execute("DELETE FROM interactions")
        await self.db.commit()
        return count

    async def clear_by_scope(
        self,
        scope: str,
        session_id: str | None = None,
    ) -> int:
        """Delete interactions by scope. Returns count deleted."""
        if scope == "all":
            return await self.clear()
        elif scope == "24h":
            cursor = await self.db.execute(
                "SELECT COUNT(*) FROM interactions WHERE timestamp >= datetime('now', '-24 hours')"
            )
            count_row = await cursor.fetchone()
            count = count_row[0] if count_row else 0
            await self.db.execute(
                "DELETE FROM interactions WHERE timestamp >= datetime('now', '-24 hours')"
            )
            await self.db.commit()
            return count
        elif scope == "session":
            if not session_id:
                # Fall back to most recent session
                cursor = await self.db.execute(
                    "SELECT session_id FROM interactions "
                    "WHERE session_id IS NOT NULL ORDER BY timestamp DESC LIMIT 1"
                )
                row = await cursor.fetchone()
                if not row:
                    return 0
                session_id = row[0]
            cursor = await self.db.execute(
                "SELECT COUNT(*) FROM interactions WHERE session_id = ?", (session_id,)
            )
            count_row = await cursor.fetchone()
            count = count_row[0] if count_row else 0
            await self.db.execute(
                "DELETE FROM interactions WHERE session_id = ?", (session_id,)
            )
            await self.db.commit()
            return count
        return 0

    async def get_session_graph(self, session_id: str) -> dict[str, Any]:
        """Compute graph data for a session (nodes, edges, timeline).

        Pass ``session_id="__unsessioned__"`` to get the graph for all
        interactions that were captured without a session prefix.
        """
        if session_id == "__unsessioned__":
            cursor = await self.db.execute(
                """
                SELECT id, timestamp, provider, model, tool_calls, status_code,
                       total_latency_ms, time_to_first_token_ms, error,
                       token_usage, cost_estimate, is_streaming, tools
                FROM interactions
                WHERE session_id IS NULL
                ORDER BY timestamp ASC
                """
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT id, timestamp, provider, model, tool_calls, status_code,
                       total_latency_ms, time_to_first_token_ms, error,
                       token_usage, cost_estimate, is_streaming, tools
                FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            )
        rows = await cursor.fetchall()
        if not rows:
            return {"nodes": [], "edges": [], "timeline": []}

        # Collect all interactions' parsed data
        interactions_data: list[dict[str, Any]] = []
        for row in rows:
            token_data = _deserialize_json(row["token_usage"])
            cost_data = _deserialize_json(row["cost_estimate"])
            tool_calls = _deserialize_json(row["tool_calls"]) or []
            tools_defs = _deserialize_json(row["tools"]) or []
            # Extract tool names from tool_calls
            tool_names: list[str] = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name") or (tc.get("function", {}) or {}).get("name")
                    if name:
                        tool_names.append(str(name))
            # Also gather tool names from definitions
            for td in tools_defs:
                if isinstance(td, dict):
                    name = td.get("name")
                    if name and name not in tool_names:
                        tool_names.append(str(name))
            interactions_data.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "provider": row["provider"],
                "model": row["model"],
                "status_code": row["status_code"],
                "total_latency_ms": row["total_latency_ms"],
                "time_to_first_token_ms": row["time_to_first_token_ms"],
                "error": row["error"],
                "is_streaming": bool(row["is_streaming"]),
                "input_tokens": token_data.get("input_tokens") if token_data else None,
                "output_tokens": token_data.get("output_tokens") if token_data else None,
                "total_cost": cost_data.get("total_cost") if cost_data else None,
                "tool_names": tool_names,
            })

        def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
            count = len(items)
            errors = sum(1 for x in items if x.get("error") or (x.get("status_code") or 200) >= 400)
            latencies = [x["total_latency_ms"] for x in items if x["total_latency_ms"] is not None]
            tokens = sum(
                (x.get("input_tokens") or 0) + (x.get("output_tokens") or 0) for x in items
            )
            cost = sum(x.get("total_cost") or 0.0 for x in items)
            sorted_lat = sorted(latencies)
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else None
            return {
                "callCount": count,
                "errorRate": round(errors / count, 4) if count else 0,
                "avgLatencyMs": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "p95LatencyMs": round(p95, 1) if p95 is not None else None,
                "totalTokens": tokens,
                "totalCostUsd": round(cost, 6),
            }

        all_metrics = _metrics(interactions_data)

        # Build unique sets
        providers = list(dict.fromkeys(x["provider"] for x in interactions_data))
        # provider -> models
        provider_models: dict[str, list[str]] = {}
        for x in interactions_data:
            p = x["provider"]
            m = x["model"]
            if m:
                provider_models.setdefault(p, [])
                if m not in provider_models[p]:
                    provider_models[p].append(m)
        all_tool_names: list[str] = []
        for x in interactions_data:
            for tn in x["tool_names"]:
                if tn not in all_tool_names:
                    all_tool_names.append(tn)

        # Nodes
        nodes: list[dict[str, Any]] = [
            {"id": "agent", "type": "agent", "label": "Agent", "metrics": all_metrics},
            {"id": "proxy", "type": "proxy", "label": "Interceptor", "metrics": all_metrics},
        ]
        for p in providers:
            p_items = [x for x in interactions_data if x["provider"] == p]
            nodes.append(
                {
                    "id": f"provider:{p}",
                    "type": "provider",
                    "label": p,
                    "metrics": _metrics(p_items),
                }
            )
        for p in providers:
            for m in provider_models.get(p, []):
                m_items = [x for x in interactions_data if x["model"] == m]
                nodes.append(
                    {"id": f"model:{m}", "type": "model", "label": m, "metrics": _metrics(m_items)}
                )
        for tn in all_tool_names:
            t_items = [x for x in interactions_data if tn in x["tool_names"]]
            nodes.append(
                {"id": f"tool:{tn}", "type": "tool", "label": tn, "metrics": _metrics(t_items)}
            )

        # Edges
        edges: list[dict[str, Any]] = []
        # Agent → Proxy
        edges.append({"from": "agent", "to": "proxy", **all_metrics})
        # Agent → Tool
        for tn in all_tool_names:
            t_items = [x for x in interactions_data if tn in x["tool_names"]]
            edges.append({"from": "agent", "to": f"tool:{tn}", **_metrics(t_items)})
        # Proxy → Provider
        for p in providers:
            p_items = [x for x in interactions_data if x["provider"] == p]
            edges.append({"from": "proxy", "to": f"provider:{p}", **_metrics(p_items)})
        # Provider → Model
        for p in providers:
            for m in provider_models.get(p, []):
                m_items = [x for x in interactions_data if x["model"] == m]
                edges.append({"from": f"provider:{p}", "to": f"model:{m}", **_metrics(m_items)})

        # Timeline
        timeline = [
            {
                "interactionId": x["id"],
                "timestamp": x["timestamp"],
                "status": x["status_code"],
                "latencyMs": x["total_latency_ms"],
                "provider": x["provider"],
                "isStreaming": x["is_streaming"],
                "error": x["error"],
            }
            for x in interactions_data
        ]

        return {"nodes": nodes, "edges": edges, "timeline": timeline}

    async def get_session_tool_sequence(self, session_id: str) -> list[dict[str, Any]]:
        """Return ordered list of interactions with tool calls and results for a session.

        Each entry represents one LLM API call and contains:
        - interactionId, interactionIndex, timestamp, model, provider, latencyMs
        - toolCalls: list of {id, name, input} from the LLM response
        - toolResults: list of {toolCallId, content} parsed from this interaction's messages
        """
        if session_id == "__unsessioned__":
            cursor = await self.db.execute(
                """
                SELECT id, timestamp, provider, model, tool_calls, messages,
                       total_latency_ms, status_code, error
                FROM interactions
                WHERE session_id IS NULL
                ORDER BY timestamp ASC
                """
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT id, timestamp, provider, model, tool_calls, messages,
                       total_latency_ms, status_code, error
                FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            )
        rows = await cursor.fetchall()

        def _extract_tool_calls(raw: Any) -> list[dict[str, Any]]:
            calls = []
            if not raw:
                return calls
            for tc in raw:
                if not isinstance(tc, dict):
                    continue
                # Anthropic format: {type: "tool_use", id, name, input}
                if tc.get("type") == "tool_use" or tc.get("name"):
                    calls.append({
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": tc.get("input") or {},
                    })
                # OpenAI format: {id, type: "function", function: {name, arguments}}
                elif "function" in tc:
                    fn = tc.get("function") or {}
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        input_data = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        input_data = {"raw": raw_args}
                    calls.append({
                        "id": tc.get("id"),
                        "name": fn.get("name"),
                        "input": input_data,
                    })
            return calls

        def _extract_tool_results(messages: Any) -> list[dict[str, Any]]:
            results = []
            if not messages:
                return results
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                # OpenAI format: role == "tool"
                if role == "tool":
                    results.append({
                        "toolCallId": msg.get("tool_call_id"),
                        "content": content if isinstance(content, str) else json.dumps(content),
                    })
                # Anthropic format: role == "user" with content list containing tool_result blocks
                elif role == "user" and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            block_content = block.get("content")
                            if isinstance(block_content, list):
                                text_parts = [
                                    b.get("text", "") for b in block_content
                                    if isinstance(b, dict) and b.get("type") == "text"
                                ]
                                content_str = "\n".join(text_parts)
                            else:
                                content_str = (
                                    block_content
                                    if isinstance(block_content, str)
                                    else json.dumps(block_content)
                                )
                            results.append({
                                "toolCallId": block.get("tool_use_id"),
                                "content": content_str,
                            })
            return results

        sequence = []
        for idx, row in enumerate(rows):
            raw_tool_calls = _deserialize_json(row["tool_calls"])
            raw_messages = _deserialize_json(row["messages"])
            tool_calls = _extract_tool_calls(raw_tool_calls)
            tool_results = _extract_tool_results(raw_messages)
            # Only include interactions that have tool calls or tool results
            if not tool_calls and not tool_results:
                continue
            sequence.append({
                "interactionId": row["id"],
                "interactionIndex": idx,
                "timestamp": row["timestamp"],
                "provider": row["provider"],
                "model": row["model"],
                "latencyMs": row["total_latency_ms"],
                "statusCode": row["status_code"],
                "error": row["error"],
                "toolCalls": tool_calls,
                "toolResults": tool_results,
            })

        return sequence

    async def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics about stored interactions."""
        cursor = await self.db.execute("SELECT COUNT(*) FROM interactions")
        total_row = await cursor.fetchone()
        total = total_row[0] if total_row else 0

        cursor = await self.db.execute(
            "SELECT provider, COUNT(*) as count FROM interactions GROUP BY provider"
        )
        provider_rows = await cursor.fetchall()
        by_provider = {row[0]: row[1] for row in provider_rows}

        cursor = await self.db.execute(
            "SELECT model, COUNT(*) as count FROM interactions "
            "WHERE model IS NOT NULL GROUP BY model ORDER BY count DESC LIMIT 10"
        )
        model_rows = await cursor.fetchall()
        by_model = {row[0]: row[1] for row in model_rows}

        cursor = await self.db.execute(
            "SELECT AVG(total_latency_ms) FROM interactions WHERE total_latency_ms IS NOT NULL"
        )
        latency_row = await cursor.fetchone()
        avg_latency = latency_row[0] if latency_row else None

        return {
            "total_interactions": total,
            "by_provider": by_provider,
            "by_model": by_model,
            "avg_latency_ms": avg_latency,
        }

    def _row_to_interaction(self, row: aiosqlite.Row) -> Interaction:
        """Convert a database row to an Interaction model."""
        token_usage_data = _deserialize_json(row["token_usage"])
        cost_data = _deserialize_json(row["cost_estimate"])
        image_data = _deserialize_json(row["image_metadata"])
        chunks_data = _deserialize_json(row["stream_chunks"])

        return Interaction(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=row["timestamp"],
            method=row["method"],
            path=row["path"],
            request_headers=json.loads(row["request_headers"]),
            request_body=_deserialize_json(row["request_body"]),
            raw_request_body=row["raw_request_body"],
            provider=Provider(row["provider"]),
            model=row["model"],
            system_prompt=row["system_prompt"],
            messages=_deserialize_json(row["messages"]),
            tools=_deserialize_json(row["tools"]),
            image_metadata=ImageMetadata(**image_data) if image_data else None,
            status_code=row["status_code"],
            response_headers=json.loads(row["response_headers"]),
            response_body=_deserialize_json(row["response_body"]),
            raw_response_body=row["raw_response_body"],
            is_streaming=bool(row["is_streaming"]),
            stream_chunks=[StreamChunk(**c) for c in chunks_data] if chunks_data else [],
            response_text=row["response_text"],
            tool_calls=_deserialize_json(row["tool_calls"]),
            token_usage=TokenUsage(**token_usage_data) if token_usage_data else None,
            cost_estimate=CostEstimate(**cost_data) if cost_data else None,
            time_to_first_token_ms=row["time_to_first_token_ms"],
            total_latency_ms=row["total_latency_ms"],
            error=row["error"],
        )
