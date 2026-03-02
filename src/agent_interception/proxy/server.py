"""Starlette application assembly and lifecycle."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent_interception.config import InterceptorConfig
from agent_interception.providers.registry import ProviderRegistry
from agent_interception.proxy.handler import ProxyHandler
from agent_interception.storage.store import InteractionStore


def create_app(
    config: InterceptorConfig,
    on_interaction: Any | None = None,
) -> Starlette:
    """Create and configure the Starlette proxy application."""

    store = InteractionStore(config)
    registry = ProviderRegistry(config)
    handler: ProxyHandler | None = None
    client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        nonlocal handler, client
        await store.initialize()
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        handler = ProxyHandler(
            config=config,
            registry=registry,
            store=store,
            http_client=client,
            on_interaction=on_interaction,
        )
        yield
        if client:
            await client.aclose()
        await store.close()

    async def health(request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    async def stats(request: Request) -> JSONResponse:
        """Stats endpoint."""
        data = await store.get_stats()
        return JSONResponse(data)

    async def list_interactions(request: Request) -> JSONResponse:
        """List recent interactions."""
        limit = int(request.query_params.get("limit", "20"))
        offset = int(request.query_params.get("offset", "0"))
        provider = request.query_params.get("provider")
        model = request.query_params.get("model")
        session_id = request.query_params.get("session_id")

        interactions = await store.list_interactions(
            limit=limit,
            offset=offset,
            provider=provider,
            model=model,
            session_id=session_id,
        )
        return JSONResponse(
            [
                {
                    "id": i.id,
                    "session_id": i.session_id,
                    "timestamp": i.timestamp.isoformat(),
                    "provider": i.provider.value,
                    "model": i.model,
                    "method": i.method,
                    "path": i.path,
                    "status_code": i.status_code,
                    "is_streaming": i.is_streaming,
                    "total_latency_ms": i.total_latency_ms,
                    "response_text_preview": (
                        i.response_text[:200] + "..."
                        if i.response_text and len(i.response_text) > 200
                        else i.response_text
                    ),
                }
                for i in interactions
            ]
        )

    async def list_sessions(request: Request) -> JSONResponse:
        """List all sessions."""
        sessions = await store.list_sessions()
        return JSONResponse(sessions)

    async def get_interaction(request: Request) -> Response:
        """Get a single interaction by ID."""
        interaction_id = request.path_params["interaction_id"]
        interaction = await store.get(interaction_id)
        if interaction is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(interaction.model_dump(mode="json"))

    async def clear_interactions(request: Request) -> JSONResponse:
        """Delete all interactions."""
        count = await store.clear()
        return JSONResponse({"deleted": count})

    async def list_conversations(request: Request) -> JSONResponse:
        """List all conversation threads with aggregate info."""
        conversations = await store.list_conversations()
        return JSONResponse(conversations)

    async def get_conversation(request: Request) -> JSONResponse:
        """Get all turns in a conversation thread."""
        conversation_id = request.path_params["conversation_id"]
        turns = await store.get_conversation(conversation_id)
        if not turns:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(
            [
                {
                    "id": t.id,
                    "session_id": t.session_id,
                    "turn_number": t.turn_number,
                    "turn_type": t.turn_type,
                    "timestamp": t.timestamp.isoformat(),
                    "provider": t.provider.value,
                    "model": t.model,
                    "parent_interaction_id": t.parent_interaction_id,
                    "context_metrics": t.context_metrics.model_dump()
                    if t.context_metrics
                    else None,
                    "response_text_preview": (
                        t.response_text[:200] + "..."
                        if t.response_text and len(t.response_text) > 200
                        else t.response_text
                    ),
                    "tool_calls": t.tool_calls,
                    "total_latency_ms": t.total_latency_ms,
                }
                for t in turns
            ]
        )

    async def proxy_catchall(request: Request) -> Response:
        """Catch-all handler that proxies requests to upstream providers."""
        assert handler is not None, "App not initialized"
        return await handler.handle(request)

    routes = [
        Route("/_interceptor/health", health, methods=["GET"]),
        Route("/_interceptor/stats", stats, methods=["GET"]),
        Route("/_interceptor/sessions", list_sessions, methods=["GET"]),
        Route("/_interceptor/interactions", list_interactions, methods=["GET"]),
        Route("/_interceptor/interactions", clear_interactions, methods=["DELETE"]),
        Route(
            "/_interceptor/interactions/{interaction_id}",
            get_interaction,
            methods=["GET"],
        ),
        Route("/_interceptor/conversations", list_conversations, methods=["GET"]),
        Route(
            "/_interceptor/conversations/{conversation_id}",
            get_conversation,
            methods=["GET"],
        ),
        # Catch-all proxy route — must be last
        Route(
            "/{path:path}",
            proxy_catchall,
            methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        ),
    ]

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
    )

    return app
