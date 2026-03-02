"""Click CLI for the interceptor proxy."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click
import uvicorn

from agent_interception.config import InterceptorConfig


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """LLM Agent Interceptor - transparent proxy for logging LLM interactions."""


@cli.command()
@click.option("--host", default=None, help="Host to bind to (default: 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Port to bind to (default: 8080)")
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
@click.option("--openai-url", default=None, help="OpenAI upstream base URL")
@click.option("--anthropic-url", default=None, help="Anthropic upstream base URL")
@click.option("--ollama-url", default=None, help="Ollama upstream base URL")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose output")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress terminal output")
@click.option("--no-redact", is_flag=True, default=False, help="Disable API key redaction")
@click.option("--no-store-chunks", is_flag=True, default=False, help="Don't store stream chunks")
def start(
    host: str | None,
    port: int | None,
    db_path: str | None,
    openai_url: str | None,
    anthropic_url: str | None,
    ollama_url: str | None,
    verbose: bool,
    quiet: bool,
    no_redact: bool,
    no_store_chunks: bool,
) -> None:
    """Start the interceptor proxy server."""
    # Build config from CLI overrides (env vars handled by pydantic-settings)
    overrides: dict[str, Any] = {}
    if host is not None:
        overrides["host"] = host
    if port is not None:
        overrides["port"] = port
    if db_path is not None:
        overrides["db_path"] = db_path
    if openai_url is not None:
        overrides["openai_base_url"] = openai_url
    if anthropic_url is not None:
        overrides["anthropic_base_url"] = anthropic_url
    if ollama_url is not None:
        overrides["ollama_base_url"] = ollama_url
    if verbose:
        overrides["verbose"] = True
    if quiet:
        overrides["quiet"] = True
    if no_redact:
        overrides["redact_api_keys"] = False
    if no_store_chunks:
        overrides["store_stream_chunks"] = False

    config = InterceptorConfig(**overrides)

    from agent_interception.display.terminal import TerminalDisplay
    from agent_interception.proxy.server import create_app

    display = TerminalDisplay(config)

    if not quiet:
        display.console.print(
            f"[bold]LLM Agent Interceptor[/bold] starting on "
            f"[green]http://{config.host}:{config.port}[/green]"
        )
        display.console.print(f"  Database: {config.db_path}")
        display.console.print(f"  OpenAI upstream: {config.openai_base_url}")
        display.console.print(f"  Anthropic upstream: {config.anthropic_base_url}")
        display.console.print(f"  Ollama upstream: {config.ollama_base_url}")
        display.console.print()

    app = create_app(config, on_interaction=display.on_interaction)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="warning" if not verbose else "info",
    )


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
@click.option("--last", "limit", default=10, type=int, help="Number of recent interactions")
@click.option("--provider", default=None, help="Filter by provider")
@click.option("--model", default=None, help="Filter by model")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full details")
def replay(
    db_path: str | None,
    limit: int,
    provider: str | None,
    model: str | None,
    verbose: bool,
) -> None:
    """Replay recent interactions from the database."""
    asyncio.run(_replay(db_path, limit, provider, model, verbose))


async def _replay(
    db_path: str | None,
    limit: int,
    provider: str | None,
    model: str | None,
    verbose: bool,
) -> None:
    overrides: dict[str, Any] = {"verbose": verbose}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.display.terminal import TerminalDisplay
    from agent_interception.storage.store import InteractionStore

    display = TerminalDisplay(config)
    store = InteractionStore(config)
    await store.initialize()

    try:
        interactions = await store.list_interactions(limit=limit, provider=provider, model=model)
        if not interactions:
            display.console.print("[dim]No interactions found.[/dim]")
            return

        # Display oldest first
        for interaction in reversed(interactions):
            display._display_interaction(interaction)
    finally:
        await store.close()


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
@click.option("--last", "limit", default=50, type=int, help="Number of interactions to export")
@click.option("--provider", default=None, help="Filter by provider")
@click.option("--model", default=None, help="Filter by model")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "jsonl"]))
def export(
    db_path: str | None,
    limit: int,
    provider: str | None,
    model: str | None,
    output: str | None,
    fmt: str,
) -> None:
    """Export interactions as JSON or JSONL."""
    asyncio.run(_export(db_path, limit, provider, model, output, fmt))


async def _export(
    db_path: str | None,
    limit: int,
    provider: str | None,
    model: str | None,
    output: str | None,
    fmt: str,
) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.storage.store import InteractionStore

    store = InteractionStore(config)
    await store.initialize()

    try:
        interactions = await store.list_interactions(limit=limit, provider=provider, model=model)

        data = [i.model_dump(mode="json") for i in interactions]

        if fmt == "json":
            text = json.dumps(data, indent=2)
        else:
            text = "\n".join(json.dumps(item) for item in data)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(text)
            click.echo(f"Exported {len(data)} interactions to {output}")
        else:
            click.echo(text)
    finally:
        await store.close()


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
def stats(db_path: str | None) -> None:
    """Show aggregate statistics."""
    asyncio.run(_stats(db_path))


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
def sessions(db_path: str | None) -> None:
    """List all captured sessions."""
    asyncio.run(_sessions(db_path))


async def _sessions(db_path: str | None) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.storage.store import InteractionStore

    store = InteractionStore(config)
    await store.initialize()

    try:
        session_list = await store.list_sessions()
        if not session_list:
            click.echo("No sessions found.")
            return

        # Also count interactions without a session
        all_interactions = await store.list_interactions(limit=999999)
        no_session = sum(1 for i in all_interactions if i.session_id is None)

        click.echo(f"\n{'SESSION ID':<32s}  {'COUNT':>5s}  {'MODELS':<40s}  {'STARTED'}")
        click.echo("-" * 100)
        for s in session_list:
            models = ", ".join(s["models"])
            click.echo(
                f"{s['session_id']:<32s}  {s['interaction_count']:>5d}  "
                f"{models:<40s}  {s['first_interaction']}"
            )
        if no_session:
            click.echo(f"\n  + {no_session} interactions without a session ID")
        click.echo()
    finally:
        await store.close()


@cli.command()
@click.argument("session_id")
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
@click.option("--output", "-o", default=None, help="Output file (default: <session_id>.json)")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "jsonl"]))
def save(session_id: str, db_path: str | None, output: str | None, fmt: str) -> None:
    """Export a session's interactions to a file."""
    asyncio.run(_save(session_id, db_path, output, fmt))


async def _save(session_id: str, db_path: str | None, output: str | None, fmt: str) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.storage.store import InteractionStore

    store = InteractionStore(config)
    await store.initialize()

    try:
        interactions = await store.list_interactions(limit=999999, session_id=session_id)
        if not interactions:
            click.echo(f"No interactions found for session '{session_id}'")
            return

        data = [i.model_dump(mode="json") for i in interactions]

        if fmt == "json":
            text = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            text = "\n".join(json.dumps(item, ensure_ascii=False) for item in data)

        out_path = output or f"{session_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)

        size_kb = len(text) / 1024
        click.echo(
            f"Exported {len(data)} interactions from session "
            f"'{session_id}' to {out_path} ({size_kb:.0f} KB)"
        )
    finally:
        await store.close()


async def _stats(db_path: str | None) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.display.terminal import TerminalDisplay
    from agent_interception.storage.store import InteractionStore

    display = TerminalDisplay(config)
    store = InteractionStore(config)
    await store.initialize()

    try:
        data = await store.get_stats()
        display.display_stats(data)
    finally:
        await store.close()


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
def conversations(db_path: str | None) -> None:
    """List all tracked conversation threads."""
    asyncio.run(_conversations(db_path))


@cli.command()
@click.option("--db", "db_path", default=None, help="Path to SQLite database")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output path (default: report.html for html; ./charts for png/svg)",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    default="html",
    type=click.Choice(["html", "png", "svg"]),
    show_default=True,
    help="Output format. HTML loads Plotly.js from CDN and requires internet to view.",
)
@click.option(
    "--last",
    "limit",
    default=200,
    type=int,
    show_default=True,
    help="Limit to last N interactions",
)
@click.option("--provider", default=None, help="Filter by provider")
@click.option("--model", default=None, help="Filter by model")
@click.option("--session", "session_id", default=None, help="Filter by session ID")
def visualize(
    db_path: str | None,
    output: str | None,
    fmt: str,
    limit: int,
    provider: str | None,
    model: str | None,
    session_id: str | None,
) -> None:
    """Generate an interactive HTML dashboard or static PNG/SVG charts."""
    asyncio.run(_visualize(db_path, output, fmt, limit, provider, model, session_id))


async def _visualize(
    db_path: str | None,
    output: str | None,
    fmt: str,
    limit: int,
    provider: str | None,
    model: str | None,
    session_id: str | None,
) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    # Resolve default output path based on format
    if output is None:
        output = "report.html" if fmt == "html" else "charts"

    from agent_interception.display.charts import export_static_charts, generate_html_report
    from agent_interception.storage.store import InteractionStore

    store = InteractionStore(config)
    await store.initialize()

    try:
        interactions = await store.list_interactions(
            limit=limit, provider=provider, model=model, session_id=session_id
        )
        if not interactions:
            click.echo("No interactions found.")
            return

        if fmt == "html":
            generate_html_report(interactions, output)
            click.echo(f"Generated HTML report: {output} ({len(interactions)} interactions)")
        else:
            export_static_charts(interactions, output, fmt)
            click.echo(
                f"Exported {fmt.upper()} charts to: {output}/ ({len(interactions)} interactions)"
            )
    finally:
        await store.close()


async def _conversations(db_path: str | None) -> None:
    overrides: dict[str, Any] = {}
    if db_path is not None:
        overrides["db_path"] = db_path

    config = InterceptorConfig(**overrides)

    from agent_interception.display.terminal import TerminalDisplay
    from agent_interception.storage.store import InteractionStore

    display = TerminalDisplay(config)
    store = InteractionStore(config)
    await store.initialize()

    try:
        conversation_list = await store.list_conversations()
        if not conversation_list:
            display.console.print("[dim]No conversations found.[/dim]")
            return
        display.display_conversations_table(conversation_list)
    finally:
        await store.close()
