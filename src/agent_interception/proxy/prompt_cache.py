"""Anthropic prompt-cache injection.

Agent SDKs that drive long tool loops re-send the same `system` + `tools`
prefix on every round. Without `cache_control` markers the full prefix is
billed as input tokens each time, which blows through per-minute rate
limits quickly (especially on Haiku's 10k TPM tier).

This module rewrites the outgoing Anthropic request body to place a single
`{"type": "ephemeral"}` cache breakpoint at the end of the stable prefix:

- If the request has tools, mark the last tool (caches system + all tools).
- Otherwise, mark the last system content block (caches just system).

Anthropic counts cached input tokens at 0.1x the normal input price, so the
effect is both cost and rate-limit relief.
"""

from __future__ import annotations

from typing import Any

from agent_interception.models import Provider

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def should_inject_prompt_cache(body: dict[str, Any], provider: Provider) -> bool:
    """True iff the request is a cacheable Anthropic Messages call."""
    if provider != Provider.ANTHROPIC:
        return False
    return bool(body.get("system")) or bool(body.get("tools"))


def inject_prompt_cache(body: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied body with one cache breakpoint on the prefix.

    Mutates neither the input dict nor any of its lists; the caller may
    freely reuse `body`.
    """
    out = dict(body)

    # Normalise string system → list of content blocks so we can attach
    # cache_control uniformly.
    system = out.get("system")
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
        out["system"] = system

    tools = out.get("tools")
    if isinstance(tools, list) and tools:
        new_tools = list(tools)
        if isinstance(new_tools[-1], dict):
            last_tool: dict[str, Any] = dict(new_tools[-1])
            last_tool["cache_control"] = dict(_EPHEMERAL)
            new_tools[-1] = last_tool
            out["tools"] = new_tools
    elif isinstance(system, list) and system:
        new_system = list(system)
        if isinstance(new_system[-1], dict):
            last_block: dict[str, Any] = dict(new_system[-1])
            last_block["cache_control"] = dict(_EPHEMERAL)
            new_system[-1] = last_block
            out["system"] = new_system

    return out
