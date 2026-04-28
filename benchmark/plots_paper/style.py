"""Shared matplotlib style + per-stage color palette for paper figures.

Importing this module configures plt.rcParams once for the process. All
figure scripts in this package import-then-use; no other module should
mutate rcParams.

The palette follows Wong (2011) "Color blindness", Nat. Methods 8, 441,
restricted to colors that print legibly on a white background. One color
per span kind, held constant across every figure so that a stack segment
labeled "llm" is always the same color whether it appears in fig 1, fig 2,
or fig 3.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

# Wong 2011 palette, restricted to high-contrast subset (yellow dropped because
# it disappears on white; black reserved for text/grid). Hex values.
PALETTE: dict[str, str] = {
    "llm": "#0072B2",          # blue
    "tool.fetch": "#D55E00",   # vermillion
    "tool.summarize": "#009E73",  # bluish-green
    "tool.search": "#E69F00",  # orange
    "retry_wait": "#999999",   # neutral gray (off-palette; deliberate non-stage tint)
    "other": "#CC79A7",        # reddish-purple (Wong); reserved for cited
                                # external data with stages that have no analog
                                # in our instrumentation (e.g. Raj et al.'s
                                # orchestration overhead bucket).
}

# Order in which stages stack from bottom to top in bar charts. Pinning this
# matters: if order varies between figures the reader has to re-anchor.
STACK_ORDER: tuple[str, ...] = (
    "llm",
    "tool.search",
    "tool.fetch",
    "tool.summarize",
    "retry_wait",
    "other",
)

# Display labels (used in legends and axis labels). Trailing periods avoided.
DISPLAY_LABEL: dict[str, str] = {
    "llm": "LLM",
    "tool.search": "Tool: search",
    "tool.fetch": "Tool: fetch",
    "tool.summarize": "Tool: summarize",
    "retry_wait": "Retry wait",
    "other": "Other (orchestration)",
}

# Default figure dimensions for column-width figures.
COLUMN_FIGSIZE: tuple[float, float] = (3.5, 2.5)
DOUBLE_COLUMN_FIGSIZE: tuple[float, float] = (7.0, 2.5)


def configure() -> None:
    """Idempotent rcParams setup. Safe to call from every figure script."""
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "DejaVu Serif", "Liberation Serif"],
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 300,
            "savefig.format": "pdf",
            "savefig.bbox": "tight",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "grid.linestyle": "--",
        }
    )


# Output directory for paper figures. Imported by make_*.py scripts.
OUT_DIR = Path(__file__).resolve().parent / "out"


def save(fig, stem: str) -> tuple[Path, Path]:
    """Save fig as both PDF (vector, paper) and PNG (300 dpi, slides). Returns paths."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_DIR / f"{stem}.pdf"
    png_path = OUT_DIR / f"{stem}.png"
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=300)
    return pdf_path, png_path
