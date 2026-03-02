"""Chart generation functions for the agent interceptor visualize command."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import click
import plotly.graph_objects as go

if TYPE_CHECKING:
    from agent_interception.models import Interaction

PROVIDER_PLOT_COLORS: dict[str, str] = {
    "openai": "#22c55e",
    "anthropic": "#3b82f6",
    "ollama": "#eab308",
    "unknown": "#6b7280",
}

_CHART_NAMES = [
    "latency_over_time",
    "token_usage",
    "cumulative_cost",
    "provider_distribution",
    "context_window_growth",
    "latency_histogram",
]


def chart_latency_over_time(interactions: list[Interaction]) -> go.Figure:
    """Scatter of total latency and TTFT over time, colored by provider."""
    fig = go.Figure()
    fig.update_layout(
        title="Latency Over Time",
        xaxis_title="Time",
        yaxis_title="Latency (ms)",
        template="plotly_dark",
        legend_title="Series",
    )

    valid = [i for i in interactions if i.timestamp is not None and i.total_latency_ms is not None]
    if not valid:
        return fig

    # Group by provider for coloring
    by_provider: dict[str, list[Interaction]] = {}
    for item in valid:
        key = item.provider.value
        by_provider.setdefault(key, []).append(item)

    for provider, items in by_provider.items():
        items_sorted = sorted(items, key=lambda x: x.timestamp)
        color = PROVIDER_PLOT_COLORS.get(provider, "#6b7280")
        fig.add_trace(
            go.Scatter(
                x=[i.timestamp for i in items_sorted],
                y=[i.total_latency_ms for i in items_sorted],
                mode="markers+lines",
                name=f"{provider} (total)",
                marker=dict(color=color),
                line=dict(color=color),
            )
        )

    # TTFT as a second series (dashed)
    ttft_valid = [i for i in valid if i.time_to_first_token_ms is not None]
    if ttft_valid:
        ttft_sorted = sorted(ttft_valid, key=lambda x: x.timestamp)
        fig.add_trace(
            go.Scatter(
                x=[i.timestamp for i in ttft_sorted],
                y=[i.time_to_first_token_ms for i in ttft_sorted],
                mode="markers+lines",
                name="TTFT",
                line=dict(dash="dash", color="#f97316"),
                marker=dict(color="#f97316", symbol="diamond"),
            )
        )

    return fig


def chart_token_usage(interactions: list[Interaction]) -> go.Figure:
    """Grouped bar chart of input/output tokens per interaction."""
    fig = go.Figure()
    fig.update_layout(
        title="Token Usage",
        xaxis_title="Time",
        yaxis_title="Tokens",
        template="plotly_dark",
        barmode="group",
    )

    valid = [
        i
        for i in interactions
        if i.timestamp is not None
        and i.token_usage is not None
        and (i.token_usage.input_tokens is not None or i.token_usage.output_tokens is not None)
    ]
    if not valid:
        return fig

    valid_sorted = sorted(valid, key=lambda x: x.timestamp)
    timestamps = [i.timestamp for i in valid_sorted]
    input_tokens = [i.token_usage.input_tokens or 0 for i in valid_sorted]  # type: ignore[union-attr]
    output_tokens = [i.token_usage.output_tokens or 0 for i in valid_sorted]  # type: ignore[union-attr]

    fig.add_trace(
        go.Bar(
            x=timestamps,
            y=input_tokens,
            name="Input Tokens",
            marker_color="#3b82f6",
        )
    )
    fig.add_trace(
        go.Bar(
            x=timestamps,
            y=output_tokens,
            name="Output Tokens",
            marker_color="#22c55e",
        )
    )

    return fig


def chart_cumulative_cost(interactions: list[Interaction]) -> go.Figure:
    """Line chart of cumulative cost over time."""
    fig = go.Figure()
    fig.update_layout(
        title="Cumulative Cost (USD)",
        xaxis_title="Time",
        yaxis_title="Cumulative Cost (USD)",
        template="plotly_dark",
    )

    valid = [
        i
        for i in interactions
        if i.timestamp is not None
        and i.cost_estimate is not None
        and i.cost_estimate.total_cost is not None
    ]
    if not valid:
        return fig

    valid_sorted = sorted(valid, key=lambda x: x.timestamp)
    timestamps = [i.timestamp for i in valid_sorted]
    cumulative: list[float] = []
    running = 0.0
    for i in valid_sorted:
        running += i.cost_estimate.total_cost  # type: ignore[union-attr]
        cumulative.append(running)

    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative Cost",
            line=dict(color="#a855f7"),
            fill="tozeroy",
            fillcolor="rgba(168,85,247,0.15)",
        )
    )

    return fig


def chart_provider_distribution(interactions: list[Interaction]) -> go.Figure:
    """Pie chart (3+ providers) or horizontal bar (≤2 providers) of provider distribution."""
    fig = go.Figure()

    counts: dict[str, int] = {}
    for i in interactions:
        key = i.provider.value
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        fig.update_layout(title="Provider Distribution", template="plotly_dark")
        return fig

    providers = list(counts.keys())
    values = [counts[p] for p in providers]
    colors = [PROVIDER_PLOT_COLORS.get(p, "#6b7280") for p in providers]

    if len(providers) <= 2:
        fig.add_trace(
            go.Bar(
                x=values,
                y=providers,
                orientation="h",
                marker_color=colors,
                text=values,
                textposition="auto",
            )
        )
        fig.update_layout(
            title="Provider Distribution",
            xaxis_title="Interactions",
            template="plotly_dark",
        )
    else:
        fig.add_trace(
            go.Pie(
                labels=providers,
                values=values,
                marker=dict(colors=colors),
                textinfo="label+percent",
            )
        )
        fig.update_layout(title="Provider Distribution", template="plotly_dark")

    return fig


def chart_context_window_growth(interactions: list[Interaction]) -> go.Figure:
    """Scatter of context_depth_chars vs turn_number, one trace per conversation (cap 20)."""
    fig = go.Figure()
    fig.update_layout(
        title="Context Window Growth",
        xaxis_title="Turn Number",
        yaxis_title="Context Depth (chars)",
        template="plotly_dark",
    )

    valid = [
        i
        for i in interactions
        if i.turn_number is not None
        and i.context_metrics is not None
        and i.context_metrics.context_depth_chars is not None
    ]
    if not valid:
        return fig

    # Group by conversation_id
    by_conv: dict[str, list[Interaction]] = {}
    for i in valid:
        key = i.conversation_id or "none"
        by_conv.setdefault(key, []).append(i)

    conv_ids = list(by_conv.keys())
    main_convs = conv_ids[:20]
    other_convs = conv_ids[20:]

    for conv_id in main_convs:
        items = sorted(by_conv[conv_id], key=lambda x: x.turn_number or 0)  # type: ignore[arg-type]
        label = conv_id[:8] if conv_id != "none" else "no-conv"
        fig.add_trace(
            go.Scatter(
                x=[i.turn_number for i in items],
                y=[i.context_metrics.context_depth_chars for i in items],  # type: ignore[union-attr]
                mode="lines+markers",
                name=label,
            )
        )

    if other_convs:
        other_items: list[Interaction] = []
        for conv_id in other_convs:
            other_items.extend(by_conv[conv_id])
        other_items_sorted = sorted(other_items, key=lambda x: x.turn_number or 0)  # type: ignore[arg-type]
        fig.add_trace(
            go.Scatter(
                x=[i.turn_number for i in other_items_sorted],
                y=[i.context_metrics.context_depth_chars for i in other_items_sorted],  # type: ignore[union-attr]
                mode="markers",
                name="other",
                marker=dict(color="#6b7280", symbol="x"),
            )
        )

    return fig


def chart_latency_histogram(interactions: list[Interaction]) -> go.Figure:
    """Histogram of total_latency_ms with a mean line."""
    fig = go.Figure()
    fig.update_layout(
        title="Latency Distribution",
        xaxis_title="Total Latency (ms)",
        yaxis_title="Count",
        template="plotly_dark",
    )

    latencies = [i.total_latency_ms for i in interactions if i.total_latency_ms is not None]
    if not latencies:
        return fig

    fig.add_trace(
        go.Histogram(
            x=latencies,
            name="Latency",
            marker_color="#3b82f6",
            opacity=0.8,
        )
    )

    mean_val = sum(latencies) / len(latencies)
    fig.add_vline(
        x=mean_val,
        line_dash="dash",
        line_color="#f97316",
        annotation_text=f"mean={mean_val:.0f}ms",
        annotation_position="top right",
    )

    return fig


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agent Interceptor Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0f172a;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      padding: 1.5rem;
    }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      margin-bottom: 0.25rem;
      color: #f8fafc;
    }}
    .subtitle {{
      font-size: 0.85rem;
      color: #94a3b8;
      margin-bottom: 1.5rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
      gap: 1.25rem;
    }}
    .card {{
      background: #1e293b;
      border-radius: 0.75rem;
      padding: 0.5rem;
      border: 1px solid #334155;
    }}
  </style>
</head>
<body>
  <h1>Agent Interceptor Dashboard</h1>
  <p class="subtitle">{subtitle}</p>
  <div class="grid">
    {charts}
  </div>
</body>
</html>
"""

_CHART_TITLES = {
    "latency_over_time": "Latency Over Time",
    "token_usage": "Token Usage",
    "cumulative_cost": "Cumulative Cost",
    "provider_distribution": "Provider Distribution",
    "context_window_growth": "Context Window Growth",
    "latency_histogram": "Latency Distribution",
}


def generate_html_report(interactions: list[Interaction], output_path: str | pathlib.Path) -> None:
    """Render all six charts into a single dark-theme HTML dashboard file.

    The first chart includes Plotly.js from CDN; subsequent charts reference
    the already-loaded library. Requires internet access to view.
    """
    chart_fns = [
        ("latency_over_time", chart_latency_over_time),
        ("token_usage", chart_token_usage),
        ("cumulative_cost", chart_cumulative_cost),
        ("provider_distribution", chart_provider_distribution),
        ("context_window_growth", chart_context_window_growth),
        ("latency_histogram", chart_latency_histogram),
    ]

    chart_divs: list[str] = []
    for idx, (name, fn) in enumerate(chart_fns):
        fig = fn(interactions)
        include_js: bool | str = "cdn" if idx == 0 else False
        div_html = fig.to_html(
            full_html=False,
            include_plotlyjs=include_js,
            config={"responsive": True},
            div_id=f"chart-{name}",
        )
        chart_divs.append(f'<div class="card">{div_html}</div>')

    from datetime import UTC, datetime

    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    subtitle = f"{len(interactions)} interactions &mdash; generated {generated_at}"

    html = _HTML_TEMPLATE.format(
        subtitle=subtitle,
        charts="\n    ".join(chart_divs),
    )

    output_path = pathlib.Path(output_path)
    output_path.write_text(html, encoding="utf-8")


def export_static_charts(
    interactions: list[Interaction],
    output_dir: str | pathlib.Path,
    fmt: str,
) -> None:
    """Export each chart as a static image file (PNG or SVG).

    Requires kaleido to be installed::

        uv sync --group viz-static

    Raises:
        click.ClickException: if kaleido is not available.
    """
    try:
        import kaleido  # noqa: F401  # type: ignore[import-untyped]
    except ImportError as exc:
        raise click.ClickException(
            "Static image export requires kaleido.\n"
            "Install it with:  uv sync --group viz-static"
        ) from exc

    chart_fns = [
        ("latency_over_time", chart_latency_over_time),
        ("token_usage", chart_token_usage),
        ("cumulative_cost", chart_cumulative_cost),
        ("provider_distribution", chart_provider_distribution),
        ("context_window_growth", chart_context_window_growth),
        ("latency_histogram", chart_latency_histogram),
    ]

    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, fn in chart_fns:
        fig = fn(interactions)
        out_file = output_dir / f"{name}.{fmt}"
        fig.write_image(str(out_file))
