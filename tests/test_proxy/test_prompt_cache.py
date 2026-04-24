"""Tests for Anthropic prompt-cache injection."""

from __future__ import annotations

from agent_interception.models import Provider
from agent_interception.proxy.prompt_cache import (
    inject_prompt_cache,
    should_inject_prompt_cache,
)


class TestShouldInject:
    def test_rejects_non_anthropic(self) -> None:
        body = {"system": "hi", "tools": [{"name": "t"}]}
        assert not should_inject_prompt_cache(body, Provider.OPENAI)
        assert not should_inject_prompt_cache(body, Provider.OLLAMA)
        assert not should_inject_prompt_cache(body, Provider.UNKNOWN)

    def test_rejects_when_no_system_and_no_tools(self) -> None:
        assert not should_inject_prompt_cache({"messages": []}, Provider.ANTHROPIC)

    def test_accepts_system_only(self) -> None:
        assert should_inject_prompt_cache({"system": "hi"}, Provider.ANTHROPIC)

    def test_accepts_tools_only(self) -> None:
        assert should_inject_prompt_cache(
            {"tools": [{"name": "t"}]}, Provider.ANTHROPIC
        )

    def test_accepts_both(self) -> None:
        assert should_inject_prompt_cache(
            {"system": "hi", "tools": [{"name": "t"}]}, Provider.ANTHROPIC
        )


class TestInject:
    def test_marks_last_tool_when_tools_present(self) -> None:
        body = {
            "system": "you are helpful",
            "tools": [
                {"name": "read", "description": "read a file"},
                {"name": "write", "description": "write a file"},
            ],
        }
        out = inject_prompt_cache(body)
        assert out["tools"][0] == {"name": "read", "description": "read a file"}
        assert out["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        # System is normalised to list form.
        assert out["system"] == [{"type": "text", "text": "you are helpful"}]
        # System is NOT marked when tools are; one breakpoint covers both.
        assert "cache_control" not in out["system"][0]

    def test_marks_last_system_block_when_no_tools(self) -> None:
        body = {
            "system": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ],
        }
        out = inject_prompt_cache(body)
        assert "cache_control" not in out["system"][0]
        assert out["system"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_normalises_string_system_then_marks_it(self) -> None:
        body = {"system": "hello"}
        out = inject_prompt_cache(body)
        assert out["system"] == [
            {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
        ]

    def test_does_not_mutate_input(self) -> None:
        tools = [{"name": "a"}, {"name": "b"}]
        system = [{"type": "text", "text": "sys"}]
        body = {"system": system, "tools": tools}
        inject_prompt_cache(body)
        # Inputs unchanged.
        assert tools == [{"name": "a"}, {"name": "b"}]
        assert system == [{"type": "text", "text": "sys"}]
        assert body == {"system": system, "tools": tools}

    def test_empty_tools_list_falls_through_to_system(self) -> None:
        body = {"system": "sys", "tools": []}
        out = inject_prompt_cache(body)
        # Empty tools is not cacheable; falls back to system.
        assert out["system"][-1]["cache_control"] == {"type": "ephemeral"}
