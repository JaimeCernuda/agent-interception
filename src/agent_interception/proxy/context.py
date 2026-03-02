"""Context metrics computation for conversation tracking."""

from __future__ import annotations

import hashlib
from typing import Any

from agent_interception.models import ContextMetrics


def compute_context_metrics(
    messages: list[dict[str, Any]] | None,
    system_prompt: str | None,
    prev_message_count: int | None = None,
) -> ContextMetrics:
    """Compute context window metrics from a request's messages and system prompt.

    This is a pure function with no I/O. It counts message roles, accumulates
    character lengths (handling both string and block-list content formats),
    and hashes the system prompt for change detection.
    """
    messages = messages or []

    message_count = len(messages)
    user_turn_count = 0
    assistant_turn_count = 0
    tool_result_count = 0
    context_depth_chars = 0

    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            user_turn_count += 1
        elif role == "assistant":
            assistant_turn_count += 1
        elif role in ("tool", "tool_result"):
            tool_result_count += 1

        content = msg.get("content")
        context_depth_chars += _measure_content(content)

    sys_prompt = system_prompt or ""
    system_prompt_length = len(sys_prompt)
    context_depth_chars += system_prompt_length

    system_prompt_hash: str | None = None
    if sys_prompt:
        digest = hashlib.sha256(sys_prompt.encode("utf-8")).hexdigest()
        system_prompt_hash = digest[:16]

    new_messages_this_turn: int | None = None
    if prev_message_count is not None:
        new_messages_this_turn = message_count - prev_message_count

    return ContextMetrics(
        message_count=message_count,
        user_turn_count=user_turn_count,
        assistant_turn_count=assistant_turn_count,
        tool_result_count=tool_result_count,
        context_depth_chars=context_depth_chars,
        new_messages_this_turn=new_messages_this_turn,
        system_prompt_length=system_prompt_length,
        system_prompt_hash=system_prompt_hash,
    )


def _measure_content(content: str | list[Any] | None) -> int:
    """Recursively measure character length of message content.

    Handles both plain strings and lists of content blocks (Anthropic/OpenAI
    vision format), including nested tool_result blocks.
    """
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0

    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            total += len(block.get("text", ""))
        elif block_type in ("tool_result", "tool_use"):
            # tool_result may have nested content (string or block list)
            total += _measure_content(block.get("content"))
            # tool_use has an input dict; approximate its size
            inp = block.get("input")
            if inp:
                import json

                total += len(json.dumps(inp))
    return total
