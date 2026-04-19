"""Shared helpers for the demo scripts."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any


def _load_dotenv() -> None:
    """Load a .env file from the scripts/ directory into os.environ (if it exists)."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _patch_sdk_parser() -> None:
    """Make the SDK parser treat unknown message types as SystemMessage instead of crashing.

    The Claude CLI emits events like ``rate_limit_event`` that the SDK's
    ``parse_message`` doesn't know about.  Rather than raising
    ``MessageParseError`` (which kills the whole stream), we return a
    ``SystemMessage`` so iteration continues and the CLI can handle the
    rate-limit retry internally.
    """
    try:
        import claude_agent_sdk._internal.client as _cl
        import claude_agent_sdk._internal.message_parser as _mp
        from claude_agent_sdk.types import SystemMessage

        _orig = _mp.parse_message

        def _patched(data: dict) -> object:
            msg_type = data.get("type") if isinstance(data, dict) else None
            known = {"user", "assistant", "system", "result", "stream_event"}
            if msg_type and msg_type not in known:
                return SystemMessage(subtype=msg_type, data=data)
            return _orig(data)

        _mp.parse_message = _patched  # type: ignore[assignment]
        _cl.parse_message = _patched  # type: ignore[assignment]
    except Exception:
        pass  # if patching fails, safe_query still catches MessageParseError


_patch_sdk_parser()

# Fix Windows cp1252 encoding — allow Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Base proxy URL (without session prefix)
PROXY_URL = os.environ.get("INTERCEPTOR_URL", "http://127.0.0.1:8080")

WORK_DIR = str(Path(__file__).resolve().parent.parent)

# Resolve system CLI path (the bundled SDK binary may hang on Windows)
CLI_PATH: str | None = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")


def start_session(label: str) -> str:
    """Create a session-tagged proxy URL and set ANTHROPIC_BASE_URL.

    Returns the session ID. The base URL is set to
    http://proxy/_session/{label}-{short_uuid}/  so the proxy can
    extract the session ID and strip the prefix before routing.
    """
    session_id = f"{label}-{uuid.uuid4().hex[:8]}"
    session_url = f"{PROXY_URL}/_session/{session_id}"
    os.environ["ANTHROPIC_BASE_URL"] = session_url
    return session_id


async def safe_query(prompt: str, options: Any, max_retries: int = 3) -> AsyncIterator[Any]:
    """Wrapper around claude_agent_sdk.query that retries on rate_limit_event.

    The SDK raises MessageParseError for event types it doesn't know (e.g.
    ``rate_limit_event``). We retry with a wait on rate limits and skip truly
    unknown event types so the script doesn't crash mid-run.
    """
    from claude_agent_sdk import query
    from claude_agent_sdk._errors import MessageParseError

    for attempt in range(1, max_retries + 1):
        rate_limited = False
        try:
            async for msg in query(prompt=prompt, options=options):
                yield msg
        except MessageParseError as exc:
            msg_str = str(exc)
            if "rate_limit_event" in msg_str:
                if attempt < max_retries:
                    rate_limited = True
                    wait = 60 * attempt
                    print(f"\n[rate limit] waiting {wait}s before retry {attempt}/{max_retries}…")
                    await asyncio.sleep(wait)
                else:
                    print("\n[rate limit] max retries reached, giving up.")
                    return
            elif "Unknown message type" in msg_str:
                print(f"\n[warning] skipped unknown event: {exc}")
                return
            else:
                raise
        if not rate_limited:
            return


def banner(title: str, session_id: str | None = None) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"  Proxy: {PROXY_URL}")
    if session_id:
        print(f"  Session: {session_id}")
    if CLI_PATH:
        print(f"  CLI:   {CLI_PATH}")
    print(f"{'=' * 60}\n")
