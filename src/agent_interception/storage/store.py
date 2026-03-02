"""Async SQLite store for intercepted interactions."""

from __future__ import annotations

import json
import uuid
from typing import Any

import aiosqlite

from agent_interception.config import InterceptorConfig
from agent_interception.models import (
    ContextMetrics,
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
        """Save an interaction to the database.

        Before inserting, runs the conversation threading algorithm to link
        this turn to any previous turn in the same session or explicit conversation.
        """
        await self._resolve_threading(interaction)

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
                total_latency_ms, error,
                conversation_id, parent_interaction_id, turn_number, turn_type,
                context_metrics
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
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
                interaction.conversation_id,
                interaction.parent_interaction_id,
                interaction.turn_number,
                interaction.turn_type,
                _serialize_json(interaction.context_metrics),
            ),
        )
        await self.db.commit()

    async def _resolve_threading(self, interaction: Interaction) -> None:
        """Determine conversation_id, parent, turn_number, and turn_type before save."""
        explicit_conv_id = interaction.conversation_id is not None

        if explicit_conv_id:
            # Explicit conversation ID provided (e.g. via X-Interceptor-Conversation-Id header).
            # Find the most recent existing turn in that conversation to link against.
            existing = await self.get_conversation(interaction.conversation_id)  # type: ignore[arg-type]
            if existing:
                prev = existing[-1]
                interaction.parent_interaction_id = prev.id
                interaction.turn_number = (prev.turn_number or 1) + 1
                if prev.session_id != interaction.session_id:
                    interaction.turn_type = "handoff"
                elif prev.tool_calls and self._has_tool_results(interaction):
                    interaction.turn_type = "tool_result"
                else:
                    interaction.turn_type = "continuation"
                self._update_new_messages_delta(interaction, prev)
            else:
                interaction.turn_number = 1
                interaction.turn_type = "initial"

        elif interaction.session_id:
            # Infer threading from session history.
            recent = await self.get_recent_in_session(interaction.session_id, limit=1)
            if recent:
                prev = recent[0]
                if self._is_continuation(interaction, prev):
                    # Inherit or start the conversation chain.
                    interaction.conversation_id = prev.conversation_id or str(uuid.uuid4())
                    interaction.parent_interaction_id = prev.id
                    interaction.turn_number = (prev.turn_number or 1) + 1
                    if prev.tool_calls and self._has_tool_results(interaction):
                        interaction.turn_type = "tool_result"
                    else:
                        interaction.turn_type = "continuation"
                    self._update_new_messages_delta(interaction, prev)
                else:
                    # Not a continuation — start a fresh conversation thread.
                    interaction.conversation_id = str(uuid.uuid4())
                    interaction.turn_number = 1
                    interaction.turn_type = "initial"
            else:
                # First interaction in this session.
                interaction.conversation_id = str(uuid.uuid4())
                interaction.turn_number = 1
                interaction.turn_type = "initial"

        else:
            # No session ID and no explicit conversation ID (e.g. agent uses
            # ANTHROPIC_BASE_URL directly without a /_session/ prefix).
            # Search recent interactions globally for a content-based continuation match.
            recent = await self.list_interactions(limit=10)
            for prev in recent:
                if self._is_continuation(interaction, prev):
                    interaction.conversation_id = prev.conversation_id or str(uuid.uuid4())
                    interaction.parent_interaction_id = prev.id
                    interaction.turn_number = (prev.turn_number or 1) + 1
                    if prev.tool_calls and self._has_tool_results(interaction):
                        interaction.turn_type = "tool_result"
                    else:
                        interaction.turn_type = "continuation"
                    self._update_new_messages_delta(interaction, prev)
                    return
            # No match — start a fresh conversation thread.
            interaction.conversation_id = str(uuid.uuid4())
            interaction.turn_number = 1
            interaction.turn_type = "initial"

    @staticmethod
    def _is_continuation(interaction: Interaction, prev: Interaction) -> bool:
        """Return True if this interaction continues from prev.

        Checks two signals:
        1. The new messages array contains the previous response_text as an
           assistant message (conversation history was carried forward).
        2. The previous interaction had tool_calls and this one contains tool
           results (tool-call → tool-result turn pattern).
        """
        if not interaction.messages:
            return False

        # Signal 1: previous response text appears in an assistant message.
        if prev.response_text:
            check_text = prev.response_text[:100]
            for msg in interaction.messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text += block.get("text", "")
                    if check_text and check_text in text:
                        return True

        # Signal 2: previous had tool_calls, this one has tool results.
        return bool(prev.tool_calls and InteractionStore._has_tool_results(interaction))

    @staticmethod
    def _has_tool_results(interaction: Interaction) -> bool:
        """Return True if the interaction's messages include any tool result."""
        if not interaction.messages:
            return False
        for msg in interaction.messages:
            if msg.get("role") in ("tool", "tool_result"):
                return True
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        return True
        return False

    @staticmethod
    def _update_new_messages_delta(interaction: Interaction, prev: Interaction) -> None:
        """Set new_messages_this_turn on context_metrics using the previous turn's count."""
        if (
            interaction.context_metrics is not None
            and prev.context_metrics is not None
            and interaction.context_metrics.new_messages_this_turn is None
        ):
            delta = interaction.context_metrics.message_count - prev.context_metrics.message_count
            interaction.context_metrics.new_messages_this_turn = delta

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
        """List all sessions with summary info."""
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
        return [
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

    async def get_recent_in_session(self, session_id: str, limit: int = 1) -> list[Interaction]:
        """Return the most recent interactions from a session, newest first."""
        cursor = await self.db.execute(
            "SELECT * FROM interactions WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_interaction(row) for row in rows]

    async def list_conversations(self) -> list[dict[str, Any]]:
        """Return aggregate stats per conversation thread."""
        cursor = await self.db.execute(
            """
            SELECT
                conversation_id,
                COUNT(*) as turn_count,
                MIN(timestamp) as first_turn,
                MAX(timestamp) as last_turn,
                GROUP_CONCAT(DISTINCT provider) as providers,
                GROUP_CONCAT(DISTINCT model) as models,
                SUM(CAST(json_extract(token_usage, '$.input_tokens') AS INTEGER))
                    as total_input_tokens,
                SUM(CAST(json_extract(token_usage, '$.output_tokens') AS INTEGER))
                    as total_output_tokens
            FROM interactions
            WHERE conversation_id IS NOT NULL
            GROUP BY conversation_id
            ORDER BY first_turn DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "conversation_id": row[0],
                "turn_count": row[1],
                "first_turn": row[2],
                "last_turn": row[3],
                "providers": row[4].split(",") if row[4] else [],
                "models": [m for m in (row[5] or "").split(",") if m],
                "total_input_tokens": row[6],
                "total_output_tokens": row[7],
            }
            for row in rows
        ]

    async def get_conversation(self, conversation_id: str) -> list[Interaction]:
        """Return all turns of a conversation ordered by turn_number then timestamp."""
        cursor = await self.db.execute(
            """
            SELECT * FROM interactions
            WHERE conversation_id = ?
            ORDER BY COALESCE(turn_number, 0) ASC, timestamp ASC
            """,
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_interaction(row) for row in rows]

    async def clear(self) -> int:
        """Delete all interactions. Returns the number of rows deleted."""
        cursor = await self.db.execute("SELECT COUNT(*) FROM interactions")
        count_row = await cursor.fetchone()
        count = count_row[0] if count_row else 0
        await self.db.execute("DELETE FROM interactions")
        await self.db.commit()
        return count

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

        cursor = await self.db.execute(
            """
            SELECT COUNT(DISTINCT conversation_id)
            FROM interactions
            WHERE conversation_id IS NOT NULL
            """
        )
        conv_row = await cursor.fetchone()
        total_conversations = conv_row[0] if conv_row else 0

        cursor = await self.db.execute(
            """
            SELECT
                AVG(CAST(json_extract(context_metrics, '$.message_count') AS REAL)),
                AVG(CAST(json_extract(context_metrics, '$.context_depth_chars') AS REAL))
            FROM interactions
            WHERE context_metrics IS NOT NULL
            """
        )
        ctx_row = await cursor.fetchone()
        avg_message_count = ctx_row[0] if ctx_row else None
        avg_context_depth_chars = ctx_row[1] if ctx_row else None

        cursor = await self.db.execute(
            """
            SELECT COUNT(*)
            FROM interactions i
            INNER JOIN interactions prev ON prev.id = i.parent_interaction_id
            WHERE json_extract(i.context_metrics, '$.system_prompt_hash') IS NOT NULL
              AND json_extract(prev.context_metrics, '$.system_prompt_hash') IS NOT NULL
              AND json_extract(i.context_metrics, '$.system_prompt_hash')
                  != json_extract(prev.context_metrics, '$.system_prompt_hash')
            """
        )
        sp_row = await cursor.fetchone()
        system_prompt_changes = sp_row[0] if sp_row else 0

        return {
            "total_interactions": total,
            "by_provider": by_provider,
            "by_model": by_model,
            "avg_latency_ms": avg_latency,
            "total_conversations": total_conversations,
            "avg_messages_per_turn": avg_message_count,
            "avg_context_depth_chars": avg_context_depth_chars,
            "system_prompt_changes": system_prompt_changes,
        }

    def _row_to_interaction(self, row: aiosqlite.Row) -> Interaction:
        """Convert a database row to an Interaction model."""
        token_usage_data = _deserialize_json(row["token_usage"])
        cost_data = _deserialize_json(row["cost_estimate"])
        image_data = _deserialize_json(row["image_metadata"])
        chunks_data = _deserialize_json(row["stream_chunks"])
        context_metrics_data = _deserialize_json(row["context_metrics"])

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
            conversation_id=row["conversation_id"],
            parent_interaction_id=row["parent_interaction_id"],
            turn_number=row["turn_number"],
            turn_type=row["turn_type"],
            context_metrics=ContextMetrics(**context_metrics_data)
            if context_metrics_data
            else None,
        )
