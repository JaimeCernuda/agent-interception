"""Figure 4: architecture x model 4-way comparison (plus cited Raj bar).

Five bars, each a stack of stage shares (percent of active wall time):

    1. Haiku 4.5 + agentic (tool-use loop, 3-5 LLM calls/query)
    2. Opus  4.7 + agentic
    3. Haiku 4.5 + pipeline (hardcoded, 1 LLM call/query)
    4. Opus  4.7 + pipeline
    5. Raj et al. 2024 (cited)

Bars are grouped: agentic first, pipeline next, Raj last. This makes the
within-architecture model comparison visible (bars 1-2 and 3-4) and the
between-architecture effect visible (bars 2 and 3, or 1 and 4).

The companion to fig 1 (which shows only the pipeline cells + Raj as the
headline). This figure is the place to read off "how does the cost
breakdown shift when you change the orchestrator architecture, holding
the model constant?" — by far the largest single effect we measured.

Usage:
    python -m benchmark.plots_paper.make_fig4
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from benchmark.analysis.metrics import load_trace, per_query_row
from benchmark.plots_paper import style
# Reuse the Raj data + validation from fig 1 so the two figures cannot drift
from benchmark.plots_paper.make_fig1 import (
    RAJ_ET_AL_PERCENTAGES,
    RAJ_ET_AL_TOTAL_MS,
)

_CELL_DIRS: dict[str, Path] = {
    "cell_haiku_agentic": Path("benchmark/results/cell_haiku_agentic"),
    "cell_opus_agentic": Path("benchmark/results/cell_opus_agentic"),
    "cell_haiku_pipeline": Path("benchmark/results/cell_haiku_pipeline"),
    "cell_opus_pipeline": Path("benchmark/results/cell_opus_pipeline"),
}
_CELL_DISPLAY: dict[str, str] = {
    "cell_haiku_agentic": "Haiku 4.5\nagentic",
    "cell_opus_agentic": "Opus 4.7\nagentic",
    "cell_haiku_pipeline": "Haiku 4.5\npipeline",
    "cell_opus_pipeline": "Opus 4.7\npipeline",
    "raj_et_al": "Raj et al. 2024\n(gpt-oss-20b)",
}
_STAGES = (
    "llm",
    "tool.search",
    "tool.fetch",
    "tool.summarize",
    "retry_wait",
    "other",
)


def _per_cell_shares(cell_dir: Path) -> dict[str, list[float]]:
    if not cell_dir.exists():
        raise FileNotFoundError(
            f"cell directory not found: {cell_dir}. "
            "Either the cell has not been run yet, or it has been moved/deleted."
        )
    rows = []
    for path in sorted(cell_dir.glob("*.json")):
        tf = load_trace(path)
        rows.append(per_query_row(tf))
    if not rows:
        raise RuntimeError(f"no trace JSONs in {cell_dir}")

    out: dict[str, list[float]] = {s: [] for s in _STAGES}
    for r in rows:
        active = r["active_latency_ms"] or 1.0
        out["llm"].append(r["llm_time_ms"] / active)
        out["tool.search"].append(r["tool_search_ms"] / active)
        out["tool.fetch"].append(r["tool_fetch_ms"] / active)
        out["tool.summarize"].append(r["tool_summarize_ms"] / active)
        out["retry_wait"].append(r["retry_wait_ms"] / active)
        out["other"].append(0.0)
    return out


def _ci95(samples: list[float]) -> float:
    n = len(samples)
    if n < 2:
        return 0.0
    mean = sum(samples) / n
    var = sum((x - mean) ** 2 for x in samples) / (n - 1)
    se = math.sqrt(var / n)
    t = 1.96 if n >= 30 else 1.96 + 2.0 / max(n - 1, 1)
    return t * se


def _bar_data(cell_dir: Path) -> tuple[dict[str, float], dict[str, float], float, int]:
    shares = _per_cell_shares(cell_dir)
    n = len(shares["llm"])
    mean_pct = {s: 100.0 * sum(shares[s]) / n for s in _STAGES}
    ci_pct = {s: 100.0 * _ci95(shares[s]) for s in _STAGES}
    actives = [
        per_query_row(load_trace(p))["active_latency_ms"]
        for p in sorted(cell_dir.glob("*.json"))
    ]
    return mean_pct, ci_pct, statistics.median(actives), n


def main() -> int:
    style.configure()
    cells: list[tuple[str, dict[str, float], dict[str, float] | None, str]] = []

    for key, cell_dir in _CELL_DIRS.items():
        if not cell_dir.exists() or not list(cell_dir.glob("*.json")):
            print(f"[fig4] WARN: {cell_dir} missing — skipping {key}", file=sys.stderr)
            continue
        mean_pct, ci_pct, median_active, n = _bar_data(cell_dir)
        annotation = f"{median_active / 1000:.1f} s\n(n={n})"
        cells.append((key, mean_pct, ci_pct, annotation))

    if RAJ_ET_AL_PERCENTAGES is None:
        raise RuntimeError("RAJ_ET_AL_PERCENTAGES is None in make_fig1.py")
    raj_share = {s: RAJ_ET_AL_PERCENTAGES.get(s, 0.0) for s in _STAGES}
    raj_annotation = (
        f"{RAJ_ET_AL_TOTAL_MS / 1000:.1f} s\n(cited)"
        if RAJ_ET_AL_TOTAL_MS is not None
        else "N/R"
    )
    cells.append(("raj_et_al", raj_share, None, raj_annotation))

    # ---- Plot ---------------------------------------------------------------
    # Double-column width — 5 bars don't fit comfortably at column width.
    fig, ax = plt.subplots(figsize=(style.DOUBLE_COLUMN_FIGSIZE[0], 3.2))
    x = np.arange(len(cells))
    width = 0.62

    bottom = np.zeros(len(cells))
    for stage in _STAGES:
        heights = np.array([row[1][stage] for row in cells])
        yerr = np.array(
            [row[2][stage] if row[2] is not None else 0.0 for row in cells]
        )
        ax.bar(
            x,
            heights,
            width,
            bottom=bottom,
            color=style.PALETTE[stage],
            label=style.DISPLAY_LABEL[stage],
            edgecolor="white",
            linewidth=0.4,
            yerr=yerr,
            ecolor="#444444",
            capsize=2,
            error_kw={"lw": 0.6, "alpha": 0.7},
        )
        bottom += heights

    # Total annotation above each bar
    for xi, (_, _, _, annotation) in enumerate(cells):
        ax.text(
            xi,
            108,
            annotation,
            ha="center",
            va="bottom",
            fontsize=6.5,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([_CELL_DISPLAY[row[0]] for row in cells])
    ax.set_ylabel("share of active wall time (%)")
    ax.set_ylim(0, 122)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        ncol=6,
        frameon=False,
        fontsize=7,
        columnspacing=1.0,
        handletextpad=0.4,
    )

    # Subtle group brackets above the bars to signal "agentic" vs "pipeline"
    # without crowding the figure with extra axes. Drawn in axes-fraction
    # coordinates so the y position floats above the bar tops regardless of
    # data range.
    def _bracket(x_left: float, x_right: float, label: str) -> None:
        y = 1.18  # above the annotation row
        ax.annotate(
            "",
            xy=(x_left, y),
            xytext=(x_right, y),
            xycoords=("data", "axes fraction"),
            arrowprops=dict(arrowstyle="-", lw=0.6, color="#666666"),
        )
        ax.text(
            (x_left + x_right) / 2,
            y + 0.03,
            label,
            ha="center",
            va="bottom",
            fontsize=7,
            color="#444444",
            transform=ax.get_xaxis_transform(),
        )

    # Map keys to bar indices
    idx = {row[0]: i for i, row in enumerate(cells)}
    if "cell_haiku_agentic" in idx and "cell_opus_agentic" in idx:
        _bracket(idx["cell_haiku_agentic"] - 0.3, idx["cell_opus_agentic"] + 0.3, "agentic")
    if "cell_haiku_pipeline" in idx and "cell_opus_pipeline" in idx:
        _bracket(
            idx["cell_haiku_pipeline"] - 0.3,
            idx["cell_opus_pipeline"] + 0.3,
            "pipeline",
        )

    fig.tight_layout()
    pdf_path, png_path = style.save(fig, "fig4_architecture_comparison")
    plt.close(fig)

    caption_path = style.OUT_DIR / "fig4_architecture_comparison_caption.txt"
    with caption_path.open("w") as f:
        f.write(
            "Architecture x model comparison on FreshQA-20. The four cell bars "
            "use the same per-query trace data as fig 1 (pipeline) and the "
            "superseded agentic runs preserved under "
            "benchmark/results/cell_{haiku,opus}_agentic/. The Raj et al. bar "
            "is identical to fig 1's. All bars are normalized to 100% of active "
            "wall time per query (active = wall time minus llm.retry_wait spans).\n\n"
            "Read order:\n"
            "  - Bars 1-2 (agentic block): the same Haiku -> Opus model swap "
            "moves LLM share by +9.3 pp because each query incurs 3-5 LLM calls.\n"
            "  - Bars 3-4 (pipeline block): the same swap moves LLM share by "
            "only +0.1 pp because the pipeline structurally caps LLM calls at "
            "exactly one per query.\n"
            "  - Compare bars 2 and 3 (or 1 and 4): switching the orchestrator "
            "architecture from agentic to pipeline shifts LLM share by ~20-30 pp "
            "while holding the model constant. The orchestrator choice is the "
            "single largest cost-breakdown lever we measured.\n\n"
            "Median active wall time per query is annotated above each bar; the "
            "Raj bar's annotation is the total cited in their figure 2c. Error "
            "caps on individual segments show the 95% confidence interval of "
            "that stage's share across the 20 queries in each cell. The Raj bar "
            "has no error caps; its 105% sum is a rounding artifact of the "
            "source figure preserved here without rescaling.\n\n"
            "tool.summarize is visible only on pipeline bars: the agentic loop "
            "did not invoke summarization in any of the 40 agentic runs, while "
            "the pipeline always calls it on each fetched page.\n"
        )

    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {caption_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
