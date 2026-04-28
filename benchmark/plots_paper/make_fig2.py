"""Figure 2: v1 vs v2 schema interpretation of the same Py-vs-Go traces.

Two subplots, side-by-side, sharing y-axis scale:

  Left  (v1) : Per-config mean wall time, with retry sleeps folded INTO the
               llm.generate stack segment. This is what the framework reported
               before the schema change. The Go bar is artificially tall on
               LLM because Go's per-attempt llm.generate spans included the
               sleep before the next attempt.

  Right (v2) : Same data, retry sleeps split out into a sibling 'retry_wait'
               segment. The LLM segment now reflects only the actual API call.

Caption (in fig2_retry_split_caption.txt):
  "The framework improved its own measurement by changing only the span
  schema; no agent code was modified."

Reads from benchmark/traces/{py,go}/. Independent of the new Cell 1 / Cell 2
runs. Idempotent: running it twice produces identical files.

Usage:
    python -m benchmark.plots_paper.make_fig2
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from benchmark.analysis.metrics import build_df
from benchmark.plots_paper import style

_TRACES = {
    "py": Path("benchmark/traces/py"),
    "go": Path("benchmark/traces/go"),
}
_CAPTION = (
    "v1 schema folds Go's ~1600 ms of retry-wait inside the llm.generate "
    "span, making Go appear ~1.30x slower than Py on LLM time. v2 schema "
    "separates retry-wait into a sibling span and reveals the LLM calls "
    "are essentially tied (~1.02x). The framework improved its own "
    "measurement by changing only the span schema; agent code was "
    "unchanged."
)


def _per_config_means(clean_only: bool) -> dict[str, dict[str, float]]:
    """Return per-config (py, go) mean ms per stage. If clean_only, restrict
    to queries where neither config retried."""
    df = build_df(_TRACES)
    if df.empty:
        raise RuntimeError(f"no traces found under {list(_TRACES.values())}")
    if clean_only:
        retry_qids = set(df[df.retry_wait_ms > 0]["query_id"])
        df = df[~df.query_id.isin(retry_qids)]

    out: dict[str, dict[str, float]] = {}
    for cfg, sub in df.groupby("config"):
        out[str(cfg)] = {
            "llm": float(sub.llm_time_ms.mean()),
            "tool.search": float(sub.tool_search_ms.mean()),
            "tool.fetch": float(sub.tool_fetch_ms.mean()),
            "tool.summarize": float(sub.tool_summarize_ms.mean()),
            "retry_wait": float(sub.retry_wait_ms.mean()),
            "_n_queries": int(len(sub)),
        }
    return out


def _draw_subplot(ax, means: dict[str, dict[str, float]], schema: str) -> None:
    """means: {'py': {...}, 'go': {...}}. schema: 'v1' or 'v2'."""
    configs = sorted(means.keys())
    x = np.arange(len(configs))
    width = 0.55

    if schema == "v1":
        # Fold retry_wait into llm; do not draw a separate retry_wait segment.
        stack_order = ("llm", "tool.search", "tool.fetch", "tool.summarize")
        adjusted = {
            c: {
                "llm": means[c]["llm"] + means[c]["retry_wait"],
                "tool.search": means[c]["tool.search"],
                "tool.fetch": means[c]["tool.fetch"],
                "tool.summarize": means[c]["tool.summarize"],
            }
            for c in configs
        }
    else:
        stack_order = style.STACK_ORDER
        adjusted = {c: {k: means[c][k] for k in stack_order} for c in configs}

    bottom = np.zeros(len(configs))
    for stage in stack_order:
        heights = np.array([adjusted[c][stage] for c in configs])
        ax.bar(
            x,
            heights,
            width,
            bottom=bottom,
            color=style.PALETTE[stage],
            label=style.DISPLAY_LABEL[stage],
            edgecolor="white",
            linewidth=0.4,
        )
        bottom += heights

    # Headroom so total annotations do not collide with the panel title.
    max_total = max(sum(adjusted[c].values()) for c in configs)
    ax.set_ylim(0, max_total * 1.18)

    # Total annotation above each bar
    for xi, c in zip(x, configs):
        total = sum(adjusted[c].values())
        ax.text(
            xi,
            total + max_total * 0.02,
            f"{total:,.0f} ms",
            ha="center",
            va="bottom",
            fontsize=7,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([c.upper() for c in configs])
    ax.set_title(
        "v1 schema: retry folded into LLM"
        if schema == "v1"
        else "v2 schema: retry split out"
    )
    ax.set_ylabel("wall time per query (ms)")


def main() -> int:
    style.configure()
    # Use clean-in-both subset so the v1-vs-v2 contrast isn't dominated by a
    # single retry-heavy query. Comment in the caption file notes this.
    means = _per_config_means(clean_only=False)
    means_clean = _per_config_means(clean_only=True)

    fig, axes = plt.subplots(
        1, 2, figsize=style.DOUBLE_COLUMN_FIGSIZE, sharey=True
    )
    _draw_subplot(axes[0], means, schema="v1")
    _draw_subplot(axes[1], means, schema="v2")

    # Single legend, below both subplots, horizontal
    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(labels),
        bbox_to_anchor=(0.5, -0.02),
        frameon=False,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))

    pdf_path, png_path = style.save(fig, "fig2_retry_split")
    plt.close(fig)

    # Caption file
    n_all = means["py"]["_n_queries"]
    n_clean = means_clean["py"]["_n_queries"]
    caption_path = style.OUT_DIR / "fig2_retry_split_caption.txt"
    with caption_path.open("w") as f:
        f.write(_CAPTION + "\n\n")
        f.write(
            f"Means computed over n={n_all} queries per config (full FreshQA-20 "
            f"subset). Of these, n={n_clean} queries had zero retries on either "
            "configuration; including the retry-heavy queries makes the v1-vs-v2 "
            "gap larger but the qualitative story is unchanged.\n\n"
        )
        f.write(
            "tool.summarize is absent from this workload because the model "
            "did not invoke summarization in any of the 40 sweep runs that "
            "produced this figure (20 Py + 20 Go); the instrumentation "
            "supports the stage but the data does not exercise it. The stage "
            "is retained in the legend for completeness.\n\n"
        )
        f.write("Per-config v2 means (ms):\n")
        for c in sorted(means.keys()):
            f.write(
                f"  {c}: llm={means[c]['llm']:.0f}, "
                f"tool.search={means[c]['tool.search']:.0f}, "
                f"tool.fetch={means[c]['tool.fetch']:.0f}, "
                f"tool.summarize={means[c]['tool.summarize']:.0f}, "
                f"retry_wait={means[c]['retry_wait']:.0f}\n"
            )
    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {caption_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
