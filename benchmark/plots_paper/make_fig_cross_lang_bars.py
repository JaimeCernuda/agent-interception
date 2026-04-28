"""Per-stage cross-language stacked bars, paper style.

Two horizontal stacked bars — Python (top) and Go (bottom) — broken down
into the four instrumented stages (tool.search, tool.fetch, llm.generate,
llm.retry_wait) using the project's per-stage palette. Same data as
figure 2's right panel, redrawn horizontally so each stage's contribution
to total wall time per query reads off the bar length without summing
back from a vertical stack.

Reads as: the LLM (blue) segments are essentially identical between Py
and Go despite the per-stage ratio table in the caption suggesting Go
"loses" on fetch; the difference Go pays for two HTTP-429 retries shows
up cleanly as the gray retry_wait segment, not absorbed into LLM time
the way the v1 schema attributed it.

Style follows fig 1 / fig 2: style.PALETTE for stage colors, white bar
edges, total annotation past each bar, single fig.legend below, light
dashed x-grid only, serif font from style.configure.
"""
from __future__ import annotations

import sys

import matplotlib.pyplot as plt
import numpy as np

from benchmark.plots_paper import style

# Per-stage means in milliseconds: (Python, Go). From the legacy v2 pass
# (n=15 clean queries; 5 Go failures excluded — see EVAL_LOG / Data quality).
_VALUES_MS: dict[str, tuple[float, float]] = {
    "llm":            (3925.0, 3961.0),
    "tool.search":    (0.26, 0.02),
    "tool.fetch":     (760.0, 1003.0),
    "tool.summarize": (0.0, 0.0),  # not invoked on this workload (kept for legend)
    "retry_wait":     (0.0, 616.0),
}

# Stack order matches style.STACK_ORDER, restricted to the stages that have
# data here (drop "other" — that's only on cited external bars like Raj's).
_STACK_ORDER: tuple[str, ...] = (
    "llm",
    "tool.search",
    "tool.fetch",
    "tool.summarize",
    "retry_wait",
)
_LANG_DISPLAY = {"py": "PY", "go": "GO"}
# Bars drawn top-to-bottom; matplotlib's barh draws upward by index, so we
# put PY at index 1 (top) and GO at index 0 (bottom) and don't invert.
_LANG_ORDER = ("go", "py")


def _format_ms(v: float) -> str:
    if v == 0:
        return "0"
    if v < 1.0:
        return f"{v:.2f}"
    return f"{v:,.0f}"


def main() -> int:
    style.configure()

    fig, ax = plt.subplots(figsize=style.DOUBLE_COLUMN_FIGSIZE)

    y = np.arange(len(_LANG_ORDER))
    height = 0.55

    # Stack each stage from left to right, in style.STACK_ORDER.
    left = np.zeros(len(_LANG_ORDER))
    for stage in _STACK_ORDER:
        widths = np.array([_VALUES_MS[stage][0 if lang == "py" else 1]
                           for lang in (_LANG_ORDER[0], _LANG_ORDER[1])])
        # _LANG_ORDER is ("go", "py"); _VALUES_MS tuples are (py, go).
        widths = np.array([
            _VALUES_MS[stage][1 if lang == "go" else 0] for lang in _LANG_ORDER
        ])
        ax.barh(
            y,
            widths,
            height,
            left=left,
            color=style.PALETTE[stage],
            label=style.DISPLAY_LABEL[stage],
            edgecolor="white",
            linewidth=0.4,
        )
        # Inline ms label inside each segment if the segment is wide enough
        # to host it without overlapping its neighbour.
        for yi, w, l in zip(y, widths, left):
            if w >= 350:  # ~5% of typical total width — readable inline
                ax.text(
                    l + w / 2,
                    yi,
                    f"{_format_ms(w)} ms",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if stage in ("llm", "tool.fetch") else "#222222",
                )
        left += widths

    # Total annotation past each bar.
    totals = left.copy()
    for yi, t in zip(y, totals):
        ax.text(
            t + max(totals) * 0.01,
            yi,
            f"{t:,.0f} ms",
            ha="left",
            va="center",
            fontsize=7,
        )

    ax.set_xlim(0, max(totals) * 1.12)
    ax.set_yticks(y)
    ax.set_yticklabels([_LANG_DISPLAY[lang] for lang in _LANG_ORDER])
    ax.set_xlabel("wall time per query (ms)")
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    ax.grid(axis="y", visible=False)

    # Single legend below the chart, matching fig 1 / fig 2 layout.
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(labels),
        bbox_to_anchor=(0.5, -0.02),
        frameon=False,
        fontsize=8,
        columnspacing=1.4,
        handletextpad=0.4,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))

    pdf_path, png_path = style.save(fig, "fig_cross_lang_bars")
    plt.close(fig)

    cap = style.OUT_DIR / "fig_cross_lang_bars_caption.txt"
    py_total = sum(_VALUES_MS[s][0] for s in _STACK_ORDER)
    go_total = sum(_VALUES_MS[s][1] for s in _STACK_ORDER)
    with cap.open("w") as f:
        f.write(
            "Per-stage wall time per query, Python vs Go, on the v2 retry-"
            "aware schema. Same model, same queries, same tool chain — only "
            "the runtime differs. Bars are means across n=15 clean queries "
            "(5 Go traces from the legacy pass were credit-balance failures "
            "and are excluded; see EVAL_LOG.md / Data quality issue).\n\n"
            "Per-stage Go/Py ratios (the chart's headline numbers, kept in "
            "the caption rather than as on-figure decoration):\n"
            f"  tool.search    : 0.07x  (noise floor; both stages near zero "
            "on the static-backend legacy traces)\n"
            f"  tool.fetch     : 1.32x  (Go slower; one slow Britannica "
            "response dominates the Go fetch mean)\n"
            f"  llm.generate   : 1.01x  HEADLINE — an honest tie. The "
            "apparent Go LLM advantage in the v1 schema was retry-sleep "
            "mis-attribution.\n"
            f"  llm.retry_wait : —      (Go retried 2x on HTTP 429; Python "
            "had zero retries on this pass)\n\n"
            f"Totals: Python {py_total:,.0f} ms, Go {go_total:,.0f} ms. "
            "tool.summarize is included in the legend but is zero on this "
            "workload because the model did not invoke summarization in "
            "any of the legacy sweep runs (the instrumentation supports "
            "the stage but the data does not exercise it). Both runtimes "
            "made nearly identical decisions across queries: 3.00 vs 2.85 "
            "LLM turns/query, 2.38 vs 2.15 tool calls/query — so the "
            "comparison is on identical work, not on different choices.\n"
        )

    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
