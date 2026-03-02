"""SQLite schema DDL for the interceptor database."""

from __future__ import annotations

import aiosqlite

SCHEMA_VERSION = 3

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

    await db.commit()
