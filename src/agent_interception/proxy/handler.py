"""Core request handler: receive -> forward -> intercept -> log."""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from agent_interception.config import InterceptorConfig
from agent_interception.models import Interaction, Provider
from agent_interception.providers.base import ProviderParser
from agent_interception.providers.registry import ProviderRegistry
from agent_interception.proxy.context import compute_context_metrics
from agent_interception.proxy.streaming import (
    StreamInterceptor,
    inject_stream_options,
    should_inject_stream_options,
)
from agent_interception.storage.store import InteractionStore

# Headers to not forward (hop-by-hop + encoding headers that the proxy handles)
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Headers to strip from responses (httpx auto-decompresses, so content-encoding is stale)
STRIP_RESPONSE_HEADERS = frozenset(
    {
        "content-encoding",
        "content-length",
        "transfer-encoding",
    }
)

# Headers to strip from forwarded requests (let httpx handle encoding negotiation)
STRIP_REQUEST_HEADERS = frozenset(
    {
        "accept-encoding",
    }
)

# Patterns for sensitive header values
SENSITIVE_HEADER_PATTERNS = re.compile(r"(sk-[a-zA-Z0-9]+|x-api-key|bearer\s+\S+)", re.IGNORECASE)


def redact_headers(headers: dict[str, str], *, redact: bool = True) -> dict[str, str]:
    """Redact sensitive values from headers."""
    if not redact:
        return headers
    result = {}
    sensitive_keys = {"authorization", "x-api-key", "api-key", "openai-api-key"}
    for key, value in headers.items():
        if key.lower() in sensitive_keys:
            # Keep first 8 chars + mask the rest
            if len(value) > 12:
                result[key] = value[:12] + "***"
            else:
                result[key] = "***"
        else:
            result[key] = value
    return result


class ProxyHandler:
    """Core proxy handler that intercepts requests and responses."""

    def __init__(
        self,
        config: InterceptorConfig,
        registry: ProviderRegistry,
        store: InteractionStore,
        http_client: httpx.AsyncClient,
        on_interaction: Any | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._store = store
        self._client = http_client
        self._on_interaction = on_interaction

    async def handle(self, request: Request) -> Response:
        """Handle an incoming proxy request."""
        start_time = time.monotonic()
        request_time = datetime.now(UTC)

        # Read raw body
        raw_body = await request.body()
        path = request.url.path
        if request.url.query:
            path += f"?{request.url.query}"
        method = request.method
        request_headers = dict(request.headers)

        # Extract session ID from path prefix: /_session/{id}/...
        session_id: str | None = None
        if path.startswith("/_session/"):
            parts = path.split("/", 3)  # ['', '_session', '{id}', 'rest...']
            if len(parts) >= 4:
                session_id = parts[2]
                path = "/" + parts[3]
            elif len(parts) == 3:
                session_id = parts[2]
                path = "/"

        # Detect provider
        provider, parser, upstream_base = self._registry.detect(path, request_headers)

        # Parse request body
        body_dict: dict[str, Any] | None = None
        if raw_body:
            try:
                body_dict = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_dict = None

        # Start building the interaction
        interaction = Interaction(
            session_id=session_id,
            timestamp=request_time,
            method=method,
            path=path,
            request_headers=redact_headers(request_headers, redact=self._config.redact_api_keys),
            request_body=body_dict,
            raw_request_body=raw_body.decode("utf-8", errors="replace") if raw_body else None,
            provider=provider,
        )

        # Read explicit conversation ID from request header
        conv_id_header = request_headers.get("x-interceptor-conversation-id")
        if conv_id_header:
            interaction.conversation_id = conv_id_header

        # Parse request for provider-specific fields
        if body_dict and provider != Provider.UNKNOWN:
            parsed = parser.parse_request(body_dict)
            interaction.model = parsed.get("model")
            interaction.system_prompt = parsed.get("system_prompt")
            interaction.messages = parsed.get("messages")
            interaction.tools = parsed.get("tools")
            interaction.image_metadata = parsed.get("image_metadata")

        # Compute context metrics (new_messages_this_turn resolved later in store.save)
        interaction.context_metrics = compute_context_metrics(
            interaction.messages,
            interaction.system_prompt,
        )

        # Check if we need to inject stream_options for OpenAI
        forward_body = raw_body
        injected_usage = False
        if body_dict and should_inject_stream_options(body_dict, provider):
            modified = inject_stream_options(body_dict)
            forward_body = json.dumps(modified).encode("utf-8")
            injected_usage = True

        # Build upstream URL
        upstream_url = f"{upstream_base}{path}"

        # Build forwarded headers (remove hop-by-hop and encoding headers)
        excluded = HOP_BY_HOP_HEADERS | STRIP_REQUEST_HEADERS
        forward_headers = {k: v for k, v in request_headers.items() if k.lower() not in excluded}

        try:
            # Forward request to upstream
            upstream_request = self._client.build_request(
                method=method,
                url=upstream_url,
                headers=forward_headers,
                content=forward_body if forward_body else None,
            )
            upstream_response = await self._client.send(upstream_request, stream=True)

            interaction.status_code = upstream_response.status_code
            interaction.response_headers = dict(upstream_response.headers)

            # Check if streaming
            content_type = upstream_response.headers.get("content-type", "")
            is_streaming = (
                "text/event-stream" in content_type
                or "application/x-ndjson" in content_type
                or (
                    provider == Provider.OLLAMA
                    and "application/json" in content_type
                    and body_dict
                    and body_dict.get("stream", True)
                )
            )

            if is_streaming:
                interaction.is_streaming = True
                return await self._handle_streaming(
                    upstream_response,
                    interaction,
                    parser,
                    provider,
                    start_time,
                    injected_usage,
                )
            else:
                return await self._handle_non_streaming(
                    upstream_response,
                    interaction,
                    parser,
                    start_time,
                )

        except httpx.ConnectError as e:
            interaction.error = f"Connection error: {e}"
            interaction.total_latency_ms = (time.monotonic() - start_time) * 1000
            await self._finalize(interaction)
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=502,
                media_type="application/json",
            )
        except httpx.TimeoutException as e:
            interaction.error = f"Timeout: {e}"
            interaction.total_latency_ms = (time.monotonic() - start_time) * 1000
            await self._finalize(interaction)
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=504,
                media_type="application/json",
            )

    async def _handle_non_streaming(
        self,
        upstream_response: httpx.Response,
        interaction: Interaction,
        parser: ProviderParser,
        start_time: float,
    ) -> Response:
        """Handle a non-streaming response."""
        body_bytes = await upstream_response.aread()
        await upstream_response.aclose()
        interaction.total_latency_ms = (time.monotonic() - start_time) * 1000

        raw_text = body_bytes.decode("utf-8", errors="replace")
        interaction.raw_response_body = raw_text

        try:
            body_dict = json.loads(raw_text)
            interaction.response_body = body_dict

            if interaction.provider != Provider.UNKNOWN:
                parsed = parser.parse_response(body_dict)
                interaction.response_text = parsed.get("response_text")
                interaction.tool_calls = parsed.get("tool_calls")
                interaction.token_usage = parsed.get("token_usage")
                if parsed.get("model") and not interaction.model:
                    interaction.model = parsed["model"]

                interaction.cost_estimate = parser.estimate_cost(
                    interaction.model, interaction.token_usage
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        await self._finalize(interaction)

        # Forward response headers, removing hop-by-hop and stale encoding headers
        excluded_resp = HOP_BY_HOP_HEADERS | STRIP_RESPONSE_HEADERS
        response_headers = {
            k: v for k, v in upstream_response.headers.items() if k.lower() not in excluded_resp
        }

        return Response(
            content=body_bytes,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    async def _handle_streaming(
        self,
        upstream_response: httpx.Response,
        interaction: Interaction,
        parser: ProviderParser,
        provider: Provider,
        start_time: float,
        injected_usage: bool,
    ) -> Response:
        """Handle a streaming response."""
        interceptor = StreamInterceptor(parser, provider)

        async def stream_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in interceptor.intercept(upstream_response.aiter_bytes()):
                    yield chunk
            finally:
                await upstream_response.aclose()

                # Finalize after stream is done
                interaction.total_latency_ms = (time.monotonic() - start_time) * 1000
                if interceptor.first_chunk_time:
                    first_chunk_delta = interceptor.first_chunk_time - interaction.timestamp
                    interaction.time_to_first_token_ms = first_chunk_delta.total_seconds() * 1000

                interaction.stream_chunks = interceptor.chunks

                # Reconstruct response from chunks
                if interceptor.chunks:
                    reconstructed = parser.reconstruct_response(interceptor.chunks)
                    interaction.response_text = reconstructed.get("response_text")
                    interaction.tool_calls = reconstructed.get("tool_calls")
                    interaction.token_usage = reconstructed.get("token_usage")
                    if reconstructed.get("model") and not interaction.model:
                        interaction.model = reconstructed["model"]
                    interaction.cost_estimate = parser.estimate_cost(
                        interaction.model, interaction.token_usage
                    )

                await self._finalize(interaction)

        # Forward response headers, stripping stale encoding headers
        excluded_resp = HOP_BY_HOP_HEADERS | STRIP_RESPONSE_HEADERS
        response_headers = {
            k: v for k, v in upstream_response.headers.items() if k.lower() not in excluded_resp
        }

        return StreamingResponse(
            content=stream_body(),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    async def _finalize(self, interaction: Interaction) -> None:
        """Save the interaction and notify listeners."""
        await self._store.save(interaction)
        if self._on_interaction:
            with contextlib.suppress(Exception):
                await self._on_interaction(interaction)
