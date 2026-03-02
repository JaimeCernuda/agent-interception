"""Rich terminal display for intercepted interactions."""

from __future__ import annotations

import contextlib
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent_interception.config import InterceptorConfig
from agent_interception.models import Interaction, Provider

PROVIDER_COLORS: dict[Provider, str] = {
    Provider.OPENAI: "green",
    Provider.ANTHROPIC: "blue",
    Provider.OLLAMA: "yellow",
    Provider.UNKNOWN: "dim",
}


class TerminalDisplay:
    """Rich terminal display for real-time interaction logging."""

    def __init__(self, config: InterceptorConfig) -> None:
        self._config = config
        self._console = Console()

    @property
    def console(self) -> Console:
        """Access the underlying Rich console."""
        return self._console

    async def on_interaction(self, interaction: Interaction) -> None:
        """Display a completed interaction in the terminal."""
        if self._config.quiet:
            return
        try:
            self._display_interaction(interaction)
        except (UnicodeEncodeError, OSError):
            # Fallback for terminals that can't render certain characters (e.g. Windows cp1252)
            with contextlib.suppress(Exception):
                self._console.print(
                    f"[dim]{interaction.provider.value}[/dim] "
                    f"{interaction.method} {interaction.path} "
                    f"status={interaction.status_code}"
                )

    def _display_interaction(self, interaction: Interaction) -> None:
        """Render an interaction to the terminal."""
        color = PROVIDER_COLORS.get(interaction.provider, "dim")

        # Header line
        status_icon = self._status_icon(interaction.status_code)
        header = Text()
        header.append(f"{status_icon} ", style="bold")
        header.append(f"{interaction.provider.value.upper()}", style=f"bold {color}")
        header.append(f" {interaction.method} {interaction.path}", style="dim")

        if interaction.model:
            header.append(f"  model={interaction.model}", style=color)
        if interaction.is_streaming:
            header.append("  [stream]", style="italic dim")

        # Metrics line
        metrics = Text()
        if interaction.status_code:
            style = "bold" if interaction.status_code >= 400 else ""
            metrics.append(f"status={interaction.status_code}  ", style=style)
        if interaction.total_latency_ms is not None:
            metrics.append(f"latency={interaction.total_latency_ms:.0f}ms  ")
        if interaction.time_to_first_token_ms is not None:
            metrics.append(f"ttft={interaction.time_to_first_token_ms:.0f}ms  ")
        if interaction.token_usage:
            usage = interaction.token_usage
            metrics.append(f"tokens={usage.input_tokens or 0}in/{usage.output_tokens or 0}out  ")
        if interaction.cost_estimate and interaction.cost_estimate.total_cost > 0:
            metrics.append(f"cost=${interaction.cost_estimate.total_cost:.6f}  ")

        # Build content
        content_parts: list[str] = []

        if self._config.verbose:
            if interaction.system_prompt:
                preview = _truncate(interaction.system_prompt, 200)
                content_parts.append(f"[dim]System:[/dim] {preview}")

            if interaction.messages:
                last_user = None
                for msg in reversed(interaction.messages):
                    if msg.get("role") == "user":
                        last_user = msg
                        break
                if last_user:
                    content_text = _extract_text(last_user.get("content"))
                    preview = _truncate(content_text, 300)
                    content_parts.append(f"[dim]User:[/dim] {preview}")

        if interaction.response_text:
            preview = _truncate(interaction.response_text, 300)
            content_parts.append(f"[dim]Response:[/dim] {preview}")

        if interaction.tool_calls:
            names = [
                tc.get("name") or tc.get("function", {}).get("name", "?")
                for tc in interaction.tool_calls
            ]
            content_parts.append(f"[dim]Tool calls:[/dim] {', '.join(names)}")

        if interaction.error:
            content_parts.append(f"[red]Error:[/red] {interaction.error}")

        content = "\n".join(content_parts) if content_parts else "[dim]No content[/dim]"

        # Pass renderables through Group so that the Rich Text objects (header,
        # metrics) are rendered with their styles intact.  Previously they were
        # interpolated into an f-string, which called __str__() and stripped all
        # markup and colour information.
        renderables: list[Any] = [header]
        if metrics:
            renderables.append(metrics)
        renderables.append(content)

        panel = Panel(
            Group(*renderables),
            border_style=color,
            padding=(0, 1),
        )
        self._console.print(panel)

    def display_interactions_table(self, interactions: list[Interaction]) -> None:
        """Display a table of interactions."""
        table = Table(title="Intercepted Interactions", show_lines=True)
        table.add_column("Time", style="dim", width=20)
        table.add_column("Provider", width=10)
        table.add_column("Model", width=25)
        table.add_column("Path", width=25)
        table.add_column("Status", width=8)
        table.add_column("Latency", width=10)
        table.add_column("Tokens", width=15)
        table.add_column("Response", max_width=40)

        for interaction in interactions:
            color = PROVIDER_COLORS.get(interaction.provider, "dim")
            tokens = ""
            if interaction.token_usage:
                usage = interaction.token_usage
                tokens = f"{usage.input_tokens or 0}/{usage.output_tokens or 0}"

            table.add_row(
                interaction.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                Text(interaction.provider.value, style=color),
                interaction.model or "-",
                interaction.path[:25],
                str(interaction.status_code or "-"),
                f"{interaction.total_latency_ms:.0f}ms" if interaction.total_latency_ms else "-",
                tokens or "-",
                _truncate(interaction.response_text or "-", 40),
            )

        self._console.print(table)

    def display_stats(self, stats: dict[str, Any]) -> None:
        """Display aggregate statistics."""
        self._console.print(Panel("[bold]Interceptor Statistics[/bold]"))
        self._console.print(f"  Total interactions: [bold]{stats['total_interactions']}[/bold]")

        if stats["by_provider"]:
            self._console.print("  By provider:")
            for prov, count in stats["by_provider"].items():
                color = PROVIDER_COLORS.get(Provider(prov), "dim")
                self._console.print(f"    [{color}]{prov}[/{color}]: {count}")

        if stats["by_model"]:
            self._console.print("  Top models:")
            for model, count in stats["by_model"].items():
                self._console.print(f"    {model}: {count}")

        if stats["avg_latency_ms"] is not None:
            self._console.print(f"  Avg latency: {stats['avg_latency_ms']:.0f}ms")

    @staticmethod
    def _status_icon(status_code: int | None) -> str:
        if status_code is None:
            return "?"
        if status_code < 300:
            return "+"
        if status_code < 400:
            return "~"
        return "!"


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _extract_text(content: str | list[dict[str, Any]] | None) -> str:
    """Extract plain text from message content (which may be string or list of blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") in ("image", "image_url"):
                parts.append("[image]")
    return " ".join(parts)
