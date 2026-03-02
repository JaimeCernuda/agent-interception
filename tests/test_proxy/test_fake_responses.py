"""Tests for fake LLM response builder."""

from __future__ import annotations

import json

from agent_interception.models import Provider
from agent_interception.proxy.fake_responses import build_session_required_response


class TestBuildSessionRequiredResponse:
    """Verify each provider format and message content."""

    def test_anthropic_format(self) -> None:
        resp = build_session_required_response(Provider.ANTHROPIC)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert len(body["content"]) == 1
        assert body["content"][0]["type"] == "text"
        assert "/_session/" in body["content"][0]["text"]
        assert body["usage"]["input_tokens"] == 0

    def test_openai_format(self) -> None:
        resp = build_session_required_response(Provider.OPENAI)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["object"] == "chat.completion"
        assert len(body["choices"]) == 1
        msg = body["choices"][0]["message"]
        assert msg["role"] == "assistant"
        assert "/_session/" in msg["content"]
        assert body["usage"]["total_tokens"] == 0

    def test_ollama_format(self) -> None:
        resp = build_session_required_response(Provider.OLLAMA)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["done"] is True
        assert body["message"]["role"] == "assistant"
        assert "/_session/" in body["message"]["content"]

    def test_unknown_format(self) -> None:
        resp = build_session_required_response(Provider.UNKNOWN)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert "error" in body
        assert body["error"]["code"] == -32000
        assert "/_session/" in body["error"]["message"]

    def test_custom_host_in_message(self) -> None:
        resp = build_session_required_response(Provider.ANTHROPIC, host="myhost:9090")
        body = json.loads(resp.body)
        text = body["content"][0]["text"]
        assert "myhost:9090" in text
        assert "http://myhost:9090/_session/" in text

    def test_default_host(self) -> None:
        resp = build_session_required_response(Provider.OPENAI)
        body = json.loads(resp.body)
        text = body["choices"][0]["message"]["content"]
        assert "localhost:8080" in text
