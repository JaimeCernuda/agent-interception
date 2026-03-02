"""Tests for the proxy handler."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx
import pytest

from agent_interception.config import InterceptorConfig
from agent_interception.providers.registry import ProviderRegistry
from agent_interception.proxy.handler import ProxyHandler, redact_headers
from agent_interception.storage.store import InteractionStore


class TestRedactHeaders:
    def test_redacts_authorization(self) -> None:
        headers = {"authorization": "Bearer sk-1234567890abcdef"}
        result = redact_headers(headers)
        assert result["authorization"].endswith("***")
        assert "sk-1234567890" not in result["authorization"]

    def test_redacts_api_key(self) -> None:
        headers = {"x-api-key": "sk-ant-api03-verylongkey123"}
        result = redact_headers(headers)
        assert result["x-api-key"].endswith("***")

    def test_preserves_non_sensitive(self) -> None:
        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        result = redact_headers(headers)
        assert result["content-type"] == "application/json"
        assert result["anthropic-version"] == "2023-06-01"

    def test_no_redaction_when_disabled(self) -> None:
        headers = {"authorization": "Bearer sk-1234567890abcdef"}
        result = redact_headers(headers, redact=False)
        assert result["authorization"] == "Bearer sk-1234567890abcdef"

    def test_short_key_fully_masked(self) -> None:
        headers = {"x-api-key": "short"}
        result = redact_headers(headers)
        assert result["x-api-key"] == "***"


@pytest.fixture
async def handler_deps(
    tmp_path: object,
) -> AsyncGenerator[tuple[ProxyHandler, InteractionStore], None]:
    """Create a ProxyHandler with real registry and in-memory store."""
    config = InterceptorConfig(db_path=str(tmp_path / "test.db"))  # type: ignore[operator]
    store = InteractionStore(config)
    await store.initialize()
    registry = ProviderRegistry(config)
    http_client = httpx.AsyncClient()
    handler = ProxyHandler(
        config=config,
        registry=registry,
        store=store,
        http_client=http_client,
    )
    yield handler, store
    await http_client.aclose()
    await store.close()


class TestSessionGuard:
    """Test that requests without session ID or conversation header get rejected."""

    @pytest.mark.asyncio
    async def test_no_session_returns_fake_anthropic_response(
        self, handler_deps: tuple[ProxyHandler, InteractionStore]
    ) -> None:
        """Request to /v1/messages without session ID → fake Anthropic response."""
        handler, store = handler_deps

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/messages",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"host", b"localhost:8080"),
            ],
        }

        async def receive() -> dict[str, bytes]:
            body = json.dumps({"model": "claude-sonnet-4-20250514", "messages": []}).encode()
            return {"type": "http.request", "body": body}

        from starlette.requests import Request

        request = Request(scope, receive)
        response = await handler.handle(request)

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["type"] == "message"
        assert "/_session/" in body["content"][0]["text"]

        # Verify no interaction was saved
        interactions = await store.list_interactions()
        assert len(interactions) == 0

    @pytest.mark.asyncio
    async def test_no_session_returns_fake_openai_response(
        self, handler_deps: tuple[ProxyHandler, InteractionStore]
    ) -> None:
        """Request to /v1/chat/completions without session ID → fake OpenAI response."""
        handler, _store = handler_deps

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"host", b"localhost:8080"),
            ],
        }

        async def receive() -> dict[str, bytes]:
            body = json.dumps({"model": "gpt-4", "messages": []}).encode()
            return {"type": "http.request", "body": body}

        from starlette.requests import Request

        request = Request(scope, receive)
        response = await handler.handle(request)

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["object"] == "chat.completion"
        assert "/_session/" in body["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_session_prefix_passes_through(
        self, handler_deps: tuple[ProxyHandler, InteractionStore]
    ) -> None:
        """Request with /_session/test/ prefix should NOT get the fake response."""
        handler, _ = handler_deps

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/_session/test/v1/messages",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer sk-test"),
                (b"host", b"localhost:8080"),
            ],
        }

        async def receive() -> dict[str, bytes]:
            body = json.dumps({"model": "claude-sonnet-4-20250514", "messages": []}).encode()
            return {"type": "http.request", "body": body}

        from starlette.requests import Request

        request = Request(scope, receive)
        # This will try to connect upstream — may get 401/502/etc.
        # We just verify it does NOT return the fake session response.
        response = await handler.handle(request)

        # The key assertion: the response was NOT our fake session-required message.
        # If status_code is 200, verify the body isn't our fake response.
        if response.status_code == 200:
            body = json.loads(response.body)
            assert body.get("model") != "interceptor-proxy"
        else:
            # Any non-200 means the request was forwarded upstream (correct behavior)
            assert response.status_code != 200

    @pytest.mark.asyncio
    async def test_conv_id_header_passes_through(
        self, handler_deps: tuple[ProxyHandler, InteractionStore]
    ) -> None:
        """Request with X-Interceptor-Conversation-Id header (no session) should pass."""
        handler, _ = handler_deps

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/messages",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer sk-test"),
                (b"host", b"localhost:8080"),
                (b"x-interceptor-conversation-id", b"conv-abc"),
            ],
        }

        async def receive() -> dict[str, bytes]:
            body = json.dumps({"model": "claude-sonnet-4-20250514", "messages": []}).encode()
            return {"type": "http.request", "body": body}

        from starlette.requests import Request

        request = Request(scope, receive)
        response = await handler.handle(request)

        # The key assertion: the response was NOT our fake session-required message.
        if response.status_code == 200:
            body = json.loads(response.body)
            assert body.get("model") != "interceptor-proxy"
        else:
            assert response.status_code != 200
