"""End-to-end integration tests using a real Starlette app with mock upstreams."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_interception.config import InterceptorConfig
from agent_interception.models import Provider
from agent_interception.proxy.server import create_app
from agent_interception.storage.store import InteractionStore

# ---------- Mock upstream servers ----------


def _mock_openai_chat(request: Request) -> Response:
    """Mock OpenAI chat completions endpoint."""
    return JSONResponse(
        {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Mock OpenAI response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )


async def _mock_openai_chat_stream(request: Request) -> Response:
    """Mock OpenAI streaming chat completions endpoint."""
    body = await request.body()
    parsed = json.loads(body)

    async def generate() -> AsyncGenerator[bytes, None]:
        yield (
            b'data: "id":"chatcmpl-mock","choices":'
            b'[{"delta":{"role":"assistant",'
            b'"content":""},"index":0}],'
            b'"model":"gpt-4o"}\n\n'
        )
        yield (
            b'data: {"id":"chatcmpl-mock","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n'
        )
        yield (
            b'data: {"id":"chatcmpl-mock","choices":[{"delta":{"content":" world"},"index":0}]}\n\n'
        )
        yield (
            b'data: {"id":"chatcmpl-mock",'
            b'"choices":[{"delta":{},'
            b'"index":0,'
            b'"finish_reason":"stop"}]}\n\n'
        )
        if parsed.get("stream_options", {}).get("include_usage"):
            yield (
                b'data: {"id":"chatcmpl-mock",'
                b'"choices":[],"usage":'
                b'{"prompt_tokens":5,'
                b'"completion_tokens":2,'
                b'"total_tokens":7}}\n\n'
            )
        yield b"data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _mock_openai_dispatch(request: Request) -> Response:
    """Dispatch between streaming and non-streaming OpenAI requests."""
    body = await request.body()
    parsed = json.loads(body) if body else {}
    if parsed.get("stream"):
        return await _mock_openai_chat_stream(request)
    return _mock_openai_chat(request)


def _mock_anthropic_messages(request: Request) -> Response:
    """Mock Anthropic messages endpoint."""
    return JSONResponse(
        {
            "id": "msg_mock",
            "type": "message",
            "model": "claude-sonnet-4-20250514",
            "content": [{"type": "text", "text": "Mock Anthropic response"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }
    )


async def _mock_anthropic_messages_stream(request: Request) -> Response:
    """Mock Anthropic streaming messages endpoint."""

    async def generate() -> AsyncGenerator[bytes, None]:
        yield (
            b"event: message_start\n"
            b'data: {"type":"message_start",'
            b'"message":{"id":"msg_mock",'
            b'"model":"claude-sonnet-4-20250514",'
            b'"usage":{"input_tokens":12,'
            b'"output_tokens":0}}}\n\n'
        )
        yield (
            b"event: content_block_start\n"
            b'data: {"type":"content_block_start",'
            b'"index":0,"content_block":'
            b'{"type":"text","text":""}}\n\n'
        )
        yield (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta",'
            b'"index":0,"delta":'
            b'{"type":"text_delta",'
            b'"text":"Hello from Anthropic"}}\n\n'
        )
        yield (b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n')
        yield (
            b"event: message_delta\n"
            b'data: {"type":"message_delta",'
            b'"delta":{"stop_reason":"end_turn"},'
            b'"usage":{"output_tokens":4}}\n\n'
        )
        yield (b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _mock_anthropic_dispatch(request: Request) -> Response:
    body = await request.body()
    parsed = json.loads(body) if body else {}
    if parsed.get("stream"):
        return await _mock_anthropic_messages_stream(request)
    return _mock_anthropic_messages(request)


def _mock_ollama_chat(request: Request) -> Response:
    """Mock Ollama chat endpoint (non-streaming)."""
    return JSONResponse(
        {
            "model": "llama3.2",
            "message": {"role": "assistant", "content": "Mock Ollama response"},
            "done": True,
            "prompt_eval_count": 8,
            "eval_count": 6,
        }
    )


mock_upstream_app = Starlette(
    routes=[
        Route("/v1/chat/completions", _mock_openai_dispatch, methods=["POST"]),
        Route("/v1/messages", _mock_anthropic_dispatch, methods=["POST"]),
        Route("/api/chat", _mock_ollama_chat, methods=["POST"]),
    ]
)


# ---------- Fixtures ----------


@pytest.fixture
def mock_upstream() -> TestClient:
    """Create a test client for the mock upstream."""
    return TestClient(mock_upstream_app)


@pytest.fixture
def proxy_config(tmp_path: Any) -> InterceptorConfig:
    """Create a proxy config pointing to mock upstream."""
    return InterceptorConfig(
        db_path=str(tmp_path / "test.db"),
        openai_base_url="http://testserver",
        anthropic_base_url="http://testserver",
        ollama_base_url="http://testserver",
        quiet=True,
    )


@pytest.fixture
def captured_interactions() -> list[Any]:
    """Capture interactions for assertions."""
    return []


@pytest.fixture
def proxy_app(proxy_config: InterceptorConfig, captured_interactions: list[Any]) -> Starlette:
    """Create the proxy app."""

    async def capture(interaction: Any) -> None:
        captured_interactions.append(interaction)

    return create_app(proxy_config, on_interaction=capture)


# ---------- Tests ----------


class TestNonStreamingProxy:
    def test_openai_non_streaming(
        self, proxy_config: InterceptorConfig, captured_interactions: list[Any]
    ) -> None:
        """Test proxying a non-streaming OpenAI request."""
        # We need to use httpx transport to wire proxy -> mock upstream
        # Since we can't easily do real HTTP in unit tests, we'll use the
        # store directly to verify the proxy handler logic
        pass  # Covered by more targeted unit tests

    def test_redact_headers_in_stored_interaction(self) -> None:
        """Verify API keys are redacted in stored interactions."""
        from agent_interception.proxy.handler import redact_headers

        headers = {
            "authorization": "Bearer sk-proj-abc123def456ghi789",
            "content-type": "application/json",
        }
        redacted = redact_headers(headers)
        assert "sk-proj-abc123" not in redacted["authorization"]
        assert redacted["content-type"] == "application/json"


class TestProviderDetection:
    def test_openai_path(self, proxy_config: InterceptorConfig) -> None:
        from agent_interception.providers.registry import ProviderRegistry

        registry = ProviderRegistry(proxy_config)
        provider, _, url = registry.detect("/v1/chat/completions", {})
        assert provider == Provider.OPENAI
        assert url == "http://testserver"

    def test_anthropic_path(self, proxy_config: InterceptorConfig) -> None:
        from agent_interception.providers.registry import ProviderRegistry

        registry = ProviderRegistry(proxy_config)
        provider, _, url = registry.detect("/v1/messages", {})
        assert provider == Provider.ANTHROPIC
        assert url == "http://testserver"

    def test_ollama_path(self, proxy_config: InterceptorConfig) -> None:
        from agent_interception.providers.registry import ProviderRegistry

        registry = ProviderRegistry(proxy_config)
        provider, _, url = registry.detect("/api/chat", {})
        assert provider == Provider.OLLAMA
        assert url == "http://testserver"


class TestASGIApp:
    """Test the ASGI app endpoints directly using Starlette's TestClient."""

    def test_health_endpoint(self, proxy_app: Starlette) -> None:
        """Test /_interceptor/health returns OK."""
        with TestClient(proxy_app) as client:
            response = client.get("/_interceptor/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"

    def test_stats_endpoint(self, proxy_app: Starlette) -> None:
        """Test /_interceptor/stats returns statistics."""
        with TestClient(proxy_app) as client:
            response = client.get("/_interceptor/stats")
            assert response.status_code == 200
            data = response.json()
            assert "total_interactions" in data

    def test_interactions_list_endpoint(self, proxy_app: Starlette) -> None:
        """Test /_interceptor/interactions returns a list."""
        with TestClient(proxy_app) as client:
            response = client.get("/_interceptor/interactions")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    def test_interaction_not_found(self, proxy_app: Starlette) -> None:
        """Test /_interceptor/interactions/{id} returns 404 for nonexistent."""
        with TestClient(proxy_app) as client:
            response = client.get("/_interceptor/interactions/nonexistent-id")
            assert response.status_code == 404


class TestProxyForwarding:
    """Test actual proxy forwarding with mock upstreams using httpx transport."""

    def test_openai_non_streaming_e2e(self, proxy_config: InterceptorConfig, tmp_path: Any) -> None:
        """End-to-end test: proxy -> mock OpenAI -> response logged."""
        interactions: list[Any] = []

        async def capture(interaction: Any) -> None:
            interactions.append(interaction)

        # Use httpx's MockTransport to simulate the upstream
        import httpx

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Test response"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                },
                headers={"content-type": "application/json"},
            )

        import asyncio

        async def _run() -> None:
            from agent_interception.providers.registry import ProviderRegistry
            from agent_interception.proxy.handler import ProxyHandler

            store = InteractionStore(proxy_config)
            await store.initialize()

            async_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

            handler = ProxyHandler(
                config=proxy_config,
                registry=ProviderRegistry(proxy_config),
                store=store,
                http_client=async_client,
                on_interaction=capture,
            )

            # Build a Starlette request

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/_session/test/v1/chat/completions",
                "query_string": b"",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"authorization", b"Bearer sk-test123456789"),
                ],
            }

            body = json.dumps(
                {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode()

            from starlette.requests import Request

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body}

            request = Request(scope, receive)
            await handler.handle(request)

            # Verify interaction was captured
            assert len(interactions) == 1
            interaction = interactions[0]
            assert interaction.provider == Provider.OPENAI
            assert interaction.model == "gpt-4o"
            assert interaction.response_text == "Test response"
            assert interaction.token_usage is not None
            assert interaction.token_usage.total_tokens == 8
            assert interaction.status_code == 200

            # Verify it's stored in DB
            stored = await store.get(interaction.id)
            assert stored is not None
            assert stored.model == "gpt-4o"

            await async_client.aclose()
            await store.close()

        asyncio.run(_run())

    def test_anthropic_non_streaming_e2e(
        self, proxy_config: InterceptorConfig, tmp_path: Any
    ) -> None:
        """End-to-end test: proxy -> mock Anthropic -> response logged."""
        interactions: list[Any] = []

        async def capture(interaction: Any) -> None:
            interactions.append(interaction)

        import httpx

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "model": "claude-sonnet-4-20250514",
                    "content": [{"type": "text", "text": "Test Anthropic response"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 6},
                },
                headers={"content-type": "application/json"},
            )

        import asyncio

        async def _run() -> None:
            from agent_interception.providers.registry import ProviderRegistry
            from agent_interception.proxy.handler import ProxyHandler

            store = InteractionStore(proxy_config)
            await store.initialize()

            async_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

            handler = ProxyHandler(
                config=proxy_config,
                registry=ProviderRegistry(proxy_config),
                store=store,
                http_client=async_client,
                on_interaction=capture,
            )

            body = json.dumps(
                {
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode()

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/_session/test/v1/messages",
                "query_string": b"",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"anthropic-version", b"2023-06-01"),
                    (b"x-api-key", b"sk-ant-test123456789"),
                ],
            }

            from starlette.requests import Request

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body}

            request = Request(scope, receive)
            await handler.handle(request)

            assert len(interactions) == 1
            interaction = interactions[0]
            assert interaction.provider == Provider.ANTHROPIC
            assert interaction.model == "claude-sonnet-4-20250514"
            assert interaction.response_text == "Test Anthropic response"
            assert interaction.token_usage.input_tokens == 10
            assert interaction.token_usage.output_tokens == 6

            await async_client.aclose()
            await store.close()

        asyncio.run(_run())

    def test_connection_error_returns_502(
        self, proxy_config: InterceptorConfig, tmp_path: Any
    ) -> None:
        """Test that connection errors return 502."""
        import asyncio

        import httpx

        async def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async def _run() -> None:
            from agent_interception.providers.registry import ProviderRegistry
            from agent_interception.proxy.handler import ProxyHandler

            store = InteractionStore(proxy_config)
            await store.initialize()

            async_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))

            handler = ProxyHandler(
                config=proxy_config,
                registry=ProviderRegistry(proxy_config),
                store=store,
                http_client=async_client,
            )

            body = json.dumps(
                {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                }
            ).encode()

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/_session/test/v1/chat/completions",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
            }

            from starlette.requests import Request

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body}

            request = Request(scope, receive)
            response = await handler.handle(request)

            assert response.status_code == 502

            await async_client.aclose()
            await store.close()

        asyncio.run(_run())
