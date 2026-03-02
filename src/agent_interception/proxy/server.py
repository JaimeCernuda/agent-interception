"""Starlette application assembly and lifecycle."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

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

    async def api_get_interaction(request: Request) -> Response:
        """API endpoint: get a single interaction by ID (for UI lazy loading)."""
        interaction_id = request.path_params["interaction_id"]
        interaction = await store.get(interaction_id)
        if interaction is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(interaction.model_dump(mode="json"))

    async def api_download_interaction(request: Request) -> Response:
        """API endpoint: download interaction as JSON file."""
        interaction_id = request.path_params["interaction_id"]
        interaction = await store.get(interaction_id)
        if interaction is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        data = interaction.model_dump(mode="json")
        return JSONResponse(
            data,
            headers={
                "Content-Disposition": f'attachment; filename="interaction-{interaction_id}.json"'
            },
        )

    async def api_list_sessions(request: Request) -> JSONResponse:
        """API endpoint: list sessions with summary stats."""
        sessions = await store.list_sessions()
        return JSONResponse(
            [
                {
                    "sessionId": s["session_id"],
                    "startTime": s["first_interaction"],
                    "endTime": s["last_interaction"],
                    "interactionCount": s["interaction_count"],
                    "providers": s["providers"],
                    "models": s["models"],
                }
                for s in sessions
            ]
        )

    async def api_session_graph(request: Request) -> JSONResponse:
        """API endpoint: graph data for a session."""
        session_id = request.path_params["session_id"]
        graph = await store.get_session_graph(session_id)
        return JSONResponse(graph)

    async def api_session_tool_sequence(request: Request) -> JSONResponse:
        """API endpoint: ordered tool call sequence for a session."""
        session_id = request.path_params["session_id"]
        sequence = await store.get_session_tool_sequence(session_id)
        return JSONResponse(sequence)

    async def api_clear_interactions(request: Request) -> JSONResponse:
        """API endpoint: clear interactions by scope."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        scope = body.get("scope", "all")
        if scope not in ("all", "24h", "session"):
            return JSONResponse(
                {"error": "scope must be 'all', '24h', or 'session'"}, status_code=400
            )
        session_id = body.get("sessionId")
        count = await store.clear_by_scope(scope, session_id=session_id)
        return JSONResponse({"deleted": count, "scope": scope})

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

    # UI static files mount (only if built frontend exists)
    ui_static_dir = (Path(__file__).parent.parent / "ui" / "static").resolve()
    ui_mount: Mount | None = None
    if ui_static_dir.is_dir() and any(ui_static_dir.iterdir()):
        ui_mount = Mount("/_ui", app=StaticFiles(directory=str(ui_static_dir), html=True))

    routes: list[Route | Mount] = [
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
        # UI API endpoints
        Route("/api/sessions", api_list_sessions, methods=["GET"]),
        Route("/api/sessions/{session_id}/graph", api_session_graph, methods=["GET"]),
        Route(
            "/api/sessions/{session_id}/tool-sequence",
            api_session_tool_sequence,
            methods=["GET"],
        ),
        Route("/api/interactions/clear", api_clear_interactions, methods=["POST"]),
        Route(
            "/api/interactions/{interaction_id}",
            api_get_interaction,
            methods=["GET"],
        ),
        Route(
            "/api/interactions/{interaction_id}/download",
            api_download_interaction,
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

    if ui_mount is not None:
        # Insert UI mount before catch-all
        routes.insert(-1, ui_mount)

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
    )

    return app
