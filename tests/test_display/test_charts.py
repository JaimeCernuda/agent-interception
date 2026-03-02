"""Tests for the chart generation module."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import plotly.graph_objects as go
import pytest

from agent_interception.display.charts import (
    chart_context_window_growth,
    chart_cumulative_cost,
    chart_latency_histogram,
    chart_latency_over_time,
    chart_provider_distribution,
    chart_token_usage,
    export_static_charts,
    generate_html_report,
)
from agent_interception.models import (
    ContextMetrics,
    CostEstimate,
    Interaction,
    Provider,
    TokenUsage,
)


def _make_interaction(
    *,
    provider: Provider = Provider.ANTHROPIC,
    model: str = "claude-3-5-sonnet-20241022",
    timestamp_offset_s: float = 0,
    total_latency_ms: float | None = 500.0,
    ttft_ms: float | None = 100.0,
    input_tokens: int | None = 1000,
    output_tokens: int | None = 200,
    total_cost: float | None = 0.01,
    conversation_id: str | None = "conv-1",
    turn_number: int | None = 1,
    context_depth_chars: int | None = 5000,
) -> Interaction:
    base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    token_usage = None
    if input_tokens is not None or output_tokens is not None:
        token_usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    cost_estimate = None
    if total_cost is not None:
        cost_estimate = CostEstimate(total_cost=total_cost, input_cost=0.005, output_cost=0.005)

    context_metrics = None
    if context_depth_chars is not None:
        context_metrics = ContextMetrics(
            message_count=turn_number or 1,
            user_turn_count=1,
            assistant_turn_count=max(0, (turn_number or 1) - 1),
            tool_result_count=0,
            context_depth_chars=context_depth_chars,
            system_prompt_length=100,
        )

    return Interaction(
        method="POST",
        path="/v1/messages",
        provider=provider,
        model=model,
        timestamp=base_time + timedelta(seconds=timestamp_offset_s),
        total_latency_ms=total_latency_ms,
        time_to_first_token_ms=ttft_ms,
        token_usage=token_usage,
        cost_estimate=cost_estimate,
        conversation_id=conversation_id,
        turn_number=turn_number,
        context_metrics=context_metrics,
    )


@pytest.fixture()
def sample_interactions() -> list[Interaction]:
    return [
        _make_interaction(
            provider=Provider.ANTHROPIC,
            timestamp_offset_s=0,
            total_latency_ms=500.0,
            ttft_ms=100.0,
            input_tokens=1000,
            output_tokens=200,
            total_cost=0.01,
            conversation_id="conv-1",
            turn_number=1,
            context_depth_chars=5000,
        ),
        _make_interaction(
            provider=Provider.ANTHROPIC,
            timestamp_offset_s=60,
            total_latency_ms=800.0,
            ttft_ms=150.0,
            input_tokens=1500,
            output_tokens=300,
            total_cost=0.015,
            conversation_id="conv-1",
            turn_number=2,
            context_depth_chars=8000,
        ),
        _make_interaction(
            provider=Provider.OPENAI,
            model="gpt-4o",
            timestamp_offset_s=120,
            total_latency_ms=400.0,
            ttft_ms=80.0,
            input_tokens=800,
            output_tokens=150,
            total_cost=0.008,
            conversation_id="conv-2",
            turn_number=1,
            context_depth_chars=4000,
        ),
        _make_interaction(
            provider=Provider.OLLAMA,
            model="llama3.2",
            timestamp_offset_s=180,
            total_latency_ms=2000.0,
            ttft_ms=None,
            input_tokens=None,
            output_tokens=None,
            total_cost=None,
            conversation_id="conv-3",
            turn_number=1,
            context_depth_chars=3000,
        ),
        _make_interaction(
            provider=Provider.ANTHROPIC,
            timestamp_offset_s=240,
            total_latency_ms=600.0,
            ttft_ms=120.0,
            input_tokens=2000,
            output_tokens=400,
            total_cost=0.02,
            conversation_id="conv-1",
            turn_number=3,
            context_depth_chars=12000,
        ),
    ]


# ---------------------------------------------------------------------------
# chart_latency_over_time
# ---------------------------------------------------------------------------


def test_latency_over_time_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_latency_over_time(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_latency_over_time_empty() -> None:
    fig = chart_latency_over_time([])
    assert isinstance(fig, go.Figure)


def test_latency_over_time_includes_ttft(sample_interactions: list[Interaction]) -> None:
    fig = chart_latency_over_time(sample_interactions)
    names = [t.name for t in fig.data]
    assert any("TTFT" in (n or "") for n in names)


# ---------------------------------------------------------------------------
# chart_token_usage
# ---------------------------------------------------------------------------


def test_token_usage_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_token_usage(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_token_usage_empty() -> None:
    fig = chart_token_usage([])
    assert isinstance(fig, go.Figure)


def test_token_usage_bar_traces(sample_interactions: list[Interaction]) -> None:
    fig = chart_token_usage(sample_interactions)
    bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bar_traces) == 2


# ---------------------------------------------------------------------------
# chart_cumulative_cost
# ---------------------------------------------------------------------------


def test_cumulative_cost_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_cumulative_cost(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_cumulative_cost_empty() -> None:
    fig = chart_cumulative_cost([])
    assert isinstance(fig, go.Figure)


def test_cumulative_cost_monotonically_nondecreasing(
    sample_interactions: list[Interaction],
) -> None:
    """Values should be non-decreasing regardless of input order."""
    import itertools
    import random

    shuffled = list(sample_interactions)
    random.shuffle(shuffled)
    fig = chart_cumulative_cost(shuffled)
    y_values = list(fig.data[0].y)
    for a, b in itertools.pairwise(y_values):
        assert b >= a, f"Cumulative cost decreased: {a} -> {b}"


# ---------------------------------------------------------------------------
# chart_provider_distribution
# ---------------------------------------------------------------------------


def test_provider_distribution_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_provider_distribution(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_provider_distribution_empty() -> None:
    fig = chart_provider_distribution([])
    assert isinstance(fig, go.Figure)


def test_provider_distribution_pie_for_many_providers(
    sample_interactions: list[Interaction],
) -> None:
    # sample has 3 providers → should use Pie
    fig = chart_provider_distribution(sample_interactions)
    assert any(isinstance(t, go.Pie) for t in fig.data)


def test_provider_distribution_bar_for_few_providers() -> None:
    interactions = [
        _make_interaction(provider=Provider.ANTHROPIC),
        _make_interaction(provider=Provider.ANTHROPIC),
    ]
    fig = chart_provider_distribution(interactions)
    assert any(isinstance(t, go.Bar) for t in fig.data)


# ---------------------------------------------------------------------------
# chart_context_window_growth
# ---------------------------------------------------------------------------


def test_context_window_growth_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_context_window_growth(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_context_window_growth_empty() -> None:
    fig = chart_context_window_growth([])
    assert isinstance(fig, go.Figure)


def test_context_window_growth_excludes_no_context() -> None:
    """Interactions without context_metrics or turn_number should be excluded."""
    interactions = [
        _make_interaction(context_depth_chars=None, turn_number=None),
        _make_interaction(context_depth_chars=5000, turn_number=1),
    ]
    fig = chart_context_window_growth(interactions)
    # Should have at least 1 trace with data
    assert any(len(t.y) > 0 for t in fig.data if hasattr(t, "y") and t.y is not None)


def test_context_window_growth_caps_at_20_traces() -> None:
    """More than 20 conversations should be grouped into 'other'."""
    interactions = []
    for i in range(25):
        interactions.append(
            _make_interaction(
                conversation_id=f"conv-{i}",
                turn_number=1,
                context_depth_chars=1000 + i * 100,
            )
        )
    fig = chart_context_window_growth(interactions)
    # Should have 20 named traces + 1 "other" trace = 21 total
    assert len(fig.data) == 21
    other_traces = [t for t in fig.data if t.name == "other"]
    assert len(other_traces) == 1


# ---------------------------------------------------------------------------
# chart_latency_histogram
# ---------------------------------------------------------------------------


def test_latency_histogram_returns_figure(sample_interactions: list[Interaction]) -> None:
    fig = chart_latency_histogram(sample_interactions)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_latency_histogram_empty() -> None:
    fig = chart_latency_histogram([])
    assert isinstance(fig, go.Figure)


def test_latency_histogram_has_mean_line(sample_interactions: list[Interaction]) -> None:
    fig = chart_latency_histogram(sample_interactions)
    # Mean line is added via add_vline which appears in layout shapes
    assert len(fig.layout.shapes) >= 1 or len(fig.layout.annotations) >= 1


# ---------------------------------------------------------------------------
# generate_html_report
# ---------------------------------------------------------------------------


def test_generate_html_report_creates_file(
    sample_interactions: list[Interaction], tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "report.html"
    generate_html_report(sample_interactions, out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_generate_html_report_content(
    sample_interactions: list[Interaction], tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "report.html"
    generate_html_report(sample_interactions, out)
    content = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "Latency Over Time" in content
    assert "plotly" in content.lower()


def test_generate_html_report_empty_interactions(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "empty_report.html"
    generate_html_report([], out)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content


# ---------------------------------------------------------------------------
# export_static_charts
# ---------------------------------------------------------------------------


def test_export_static_charts_raises_without_kaleido(
    sample_interactions: list[Interaction], tmp_path: pathlib.Path
) -> None:
    """Should raise ClickException when kaleido is not installed."""
    import sys

    import click

    # Remove kaleido from sys.modules if present and patch the import
    with patch.dict(sys.modules, {"kaleido": None}), pytest.raises(
        click.ClickException, match="kaleido"
    ):
        export_static_charts(sample_interactions, tmp_path / "charts", "png")


def test_export_static_charts_creates_files(
    sample_interactions: list[Interaction], tmp_path: pathlib.Path
) -> None:
    """With kaleido mocked, should create output dir and call write_image per chart."""
    mock_kaleido = MagicMock()

    with (
        patch.dict("sys.modules", {"kaleido": mock_kaleido}),
        patch("plotly.graph_objects.Figure.write_image") as mock_write,
    ):
        out_dir = tmp_path / "charts"
        export_static_charts(sample_interactions, out_dir, "png")

        # Output directory should be created
        assert out_dir.exists()
        # write_image should be called once per chart (6 charts)
        assert mock_write.call_count == 6
        # All calls should be for .png files
        for call_args in mock_write.call_args_list:
            path_arg = call_args[0][0]
            assert path_arg.endswith(".png")


def test_export_static_charts_svg(
    sample_interactions: list[Interaction], tmp_path: pathlib.Path
) -> None:
    """SVG format should produce .svg file names."""
    mock_kaleido = MagicMock()

    with (
        patch.dict("sys.modules", {"kaleido": mock_kaleido}),
        patch("plotly.graph_objects.Figure.write_image") as mock_write,
    ):
        out_dir = tmp_path / "svg_charts"
        export_static_charts(sample_interactions, out_dir, "svg")

        for call_args in mock_write.call_args_list:
            path_arg = call_args[0][0]
            assert path_arg.endswith(".svg")
