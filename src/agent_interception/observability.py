"""Structured logging with request-scoped context.

A single call to :func:`configure_logging` sets up the ``agent_interception``
logger hierarchy. Every log record emitted by any module in the package
is automatically stamped with the currently-bound request IDs
(``session_id``, ``conversation_id``, ``interaction_id``, ``agent_role``)
so that a grep on any one of those IDs surfaces the full story of a
request.

IDs are stored in ``contextvars`` so each request (each Starlette task)
sees its own values without leaking to concurrent requests.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)
_interaction_id: ContextVar[str | None] = ContextVar("interaction_id", default=None)
_agent_role: ContextVar[str | None] = ContextVar("agent_role", default=None)

_CONTEXT_KEYS = ("session_id", "conversation_id", "interaction_id", "agent_role")

# LogRecord attributes we never copy into "extra" when emitting JSON.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
        *_CONTEXT_KEYS,
    }
)


def bind_request_context(
    *,
    session_id: str | None = None,
    conversation_id: str | None = None,
    interaction_id: str | None = None,
    agent_role: str | None = None,
) -> None:
    """Bind request-scoped IDs; every subsequent log record inherits them.

    Only non-None arguments overwrite existing values. Safe to call multiple
    times per request as more IDs become known (e.g. conversation_id is
    resolved later, during threading, than session_id).
    """
    if session_id is not None:
        _session_id.set(session_id)
    if conversation_id is not None:
        _conversation_id.set(conversation_id)
    if interaction_id is not None:
        _interaction_id.set(interaction_id)
    if agent_role is not None:
        _agent_role.set(agent_role)


def get_request_context() -> dict[str, str | None]:
    """Return the currently-bound request IDs (useful in tests / debug)."""
    return {key: globals()[f"_{key}"].get() for key in _CONTEXT_KEYS}


class _ContextFilter(logging.Filter):
    """Inject request-scoped IDs onto every LogRecord as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = _session_id.get()
        record.conversation_id = _conversation_id.get()
        record.interaction_id = _interaction_id.get()
        record.agent_role = _agent_role.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Suitable for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    """Human-readable formatter that appends bound IDs as [k=v ...]."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record)} {record.levelname:<7} {record.name}: {record.getMessage()}"
        ctx_parts = [
            f"{key}={getattr(record, key)}"
            for key in _CONTEXT_KEYS
            if getattr(record, key, None) is not None
        ]
        if ctx_parts:
            base = f"{base} [{' '.join(ctx_parts)}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def _resolve_json_default() -> bool:
    """True when stderr is not a TTY (log aggregator / file / pipe)."""
    env = os.environ.get("AGENT_INTERCEPTION_LOG_JSON")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return not sys.stderr.isatty()


def configure_logging(*, verbose: bool = False, json_output: bool | None = None) -> None:
    """Configure the ``agent_interception`` logger tree.

    Idempotent: replaces any handlers previously installed by this function.

    - ``verbose=True`` sets the level to DEBUG (otherwise INFO).
    - ``json_output`` forces the format; default is JSON when stderr is not
      a TTY, plain text otherwise. Can also be forced via the
      ``AGENT_INTERCEPTION_LOG_JSON`` env var.
    """
    use_json = _resolve_json_default() if json_output is None else json_output

    logger = logging.getLogger("agent_interception")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, "_agent_interception_handler", False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler._agent_interception_handler = True  # type: ignore[attr-defined]
    handler.addFilter(_ContextFilter())
    handler.setFormatter(JsonFormatter() if use_json else PlainFormatter())
    logger.addHandler(handler)
