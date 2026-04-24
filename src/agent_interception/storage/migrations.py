"""SQLite schema DDL for the interceptor database."""

from __future__ import annotations

import aiosqlite

SCHEMA_VERSION = 5

CREATE_INTERACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    request_headers TEXT NOT NULL DEFAULT '{}',
    request_body TEXT,
    raw_request_body TEXT,
    provider TEXT NOT NULL DEFAULT 'unknown',
    model TEXT,
    system_prompt TEXT,
    messages TEXT,
    tools TEXT,
    image_metadata TEXT,
    status_code INTEGER,
    response_headers TEXT NOT NULL DEFAULT '{}',
    response_body TEXT,
    raw_response_body TEXT,
    is_streaming INTEGER NOT NULL DEFAULT 0,
    stream_chunks TEXT,
    response_text TEXT,
    tool_calls TEXT,
    token_usage TEXT,
    cost_estimate TEXT,
    time_to_first_token_ms REAL,
    total_latency_ms REAL,
    error TEXT
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_provider ON interactions(provider);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_model ON interactions(model);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_path ON interactions(path);",
]

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


async def apply_migrations(db: aiosqlite.Connection) -> None:
    """Apply all pending migrations to the database."""

    await db.execute(CREATE_SCHEMA_VERSION_TABLE)

    cursor = await db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = await cursor.fetchone()
    current_version = row[0] if row else 0

    if current_version < 1:
        await db.execute(CREATE_INTERACTIONS_TABLE)
        for index_sql in CREATE_INDEXES:
            await db.execute(index_sql)
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))

    if current_version < 2:
        await db.execute("ALTER TABLE interactions ADD COLUMN session_id TEXT")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_session_id ON interactions(session_id)"
        )
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))

    if current_version < 3:
        await db.execute("ALTER TABLE interactions ADD COLUMN conversation_id TEXT")
        await db.execute("ALTER TABLE interactions ADD COLUMN parent_interaction_id TEXT")
        await db.execute("ALTER TABLE interactions ADD COLUMN turn_number INTEGER")
        await db.execute("ALTER TABLE interactions ADD COLUMN turn_type TEXT")
        await db.execute("ALTER TABLE interactions ADD COLUMN context_metrics TEXT")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_conversation_id "
            "ON interactions(conversation_id)"
        )
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (3,))

    if current_version < 4:
        await db.execute("ALTER TABLE interactions ADD COLUMN agent_role TEXT")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_agent_role ON interactions(agent_role)"
        )
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (4,))

    if current_version < 5:
        # ingested_spans holds OTel-style spans forwarded by instrumented agents.
        # One trace = one "session" in the analytics UI. Separate from interactions/
        # so the two feature-families stay decoupled (Path B: rich, opt-in).
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ingested_spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_id TEXT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                wall_time_ms REAL NOT NULL,
                cpu_time_ms REAL,
                attrs TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ok',
                error TEXT,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingested_spans_trace ON ingested_spans(trace_id);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingested_spans_received ON ingested_spans(received_at);"
        )
        # Companion table for top-level trace metadata (config, query_id, label).
        # Populated from the ingestion payload's top-level fields.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ingested_traces (
                trace_id TEXT PRIMARY KEY,
                config TEXT,
                query_id TEXT,
                label TEXT,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingested_traces_received ON ingested_traces(received_at);"
        )
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (5,))

    await db.commit()
