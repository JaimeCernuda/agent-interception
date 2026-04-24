"""Plots for the Py-vs-Go cross-language comparison.

  plot_2: stacked per-query bars, one panel per config (shared y-axis).
  plot_3: small multiples - distributions of num_tool_calls, num_retries,
          num_parse_errors per config. Semantic signals invisible to perf/RAPL.
  plot_4: per-stage latency ratio Go / Py (log-scale bar). Money shot for
          the cross-language claim: WHICH stage shifts, by how much.

Plot 1 (paper Figure 2c reproduction) was dropped when the scope pivoted
from vLLM-reproduction to Py/Go language comparison.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from benchmark.analysis.metrics import build_df

# Color map: keep hatching + brown LexRank for continuity with the paper-style plot.
COLORS = {
    "tool_search_ms":    ("#888888", None),
    "tool_fetch_ms":     ("#4477cc", "//"),
    "tool_summarize_ms": ("#8B5A2B", None),
    "llm_time_ms":       ("#D86CA2", None),
    "framework_overhead_ms": ("#cccccc", "xx"),
}

LEGEND_LABELS = {
    "tool_search_ms": "tool.search",
    "tool_fetch_ms": "tool.fetch",
    "tool_summarize_ms": "tool.summarize (LexRank)",
    "llm_time_ms": "llm.generate",
    "framework_overhead_ms": "overhead",
}

SEGMENT_ORDER = [
    "tool_search_ms",
    "tool_fetch_ms",
    "tool_summarize_ms",
    "llm_time_ms",
    "framework_overhead_ms",
]


def _stacked_bars(ax, sub: pd.DataFrame, title: str, show_legend: bool = True) -> None:
    sub = sub.sort_values("query_id").reset_index(drop=True)
    xs = range(len(sub))
    bottoms = [0.0] * len(sub)
    for col in SEGMENT_ORDER:
        vals = sub[col].tolist()
        color, hatch = COLORS[col]
        ax.bar(
            xs, vals, bottom=bottoms,
            color=color, hatch=hatch,
            edgecolor="black" if hatch else "none",
            linewidth=0.3,
            label=LEGEND_LABELS[col],
        )
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_xticks(list(xs))
    ax.set_xticklabels(sub["query_id"].tolist(), rotation=75, fontsize=7)
    ax.set_ylabel("latency (ms)")
    ax.set_title(title)
    if show_legend:
        ax.legend(fontsize=7, loc="upper right")


def plot_2(df: pd.DataFrame, out: Path, configs: list[str]) -> None:
    """Side-by-side stacked bars, one panel per config, shared y-axis."""
    present = [c for c in configs if not df[df["config"] == c].empty]
    if not present:
        print("plot_2: no data; skipping")
        return
    fig, axes = plt.subplots(
        1, len(present), figsize=(max(9, 3.5 * len(present)), 4.5), sharey=True
    )
    if len(present) == 1:
        axes = [axes]
    for ax, cfg in zip(axes, present):
        sub = df[df["config"] == cfg]
        _stacked_bars(ax, sub, f"Config {cfg}", show_legend=(cfg == present[-1]))
    fig.suptitle("Per-query cost breakdown: " + " vs ".join(present))
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


def plot_3(df: pd.DataFrame, out: Path, configs: list[str]) -> None:
    """Small-multiples histograms: tool calls / retries / parse errors per config."""
    present = [c for c in configs if not df[df["config"] == c].empty]
    if not present:
        print("plot_3: no data; skipping")
        return
    metrics = [
        ("num_tool_calls", "Tool calls per query"),
        ("num_retries", "Tool retries per query"),
        ("num_parse_errors", "LLM parse errors per query"),
    ]
    fig, axes = plt.subplots(
        len(metrics), len(present),
        figsize=(max(6, 2.5 * len(present)), 2.2 * len(metrics)),
        sharey="row",
    )
    if len(present) == 1:
        axes = np.array(axes).reshape(-1, 1)
    for i, (col, title) in enumerate(metrics):
        vmax = int(max(1, df[col].max()))
        for j, cfg in enumerate(present):
            ax = axes[i][j]
            sub = df[df["config"] == cfg][col]
            ax.hist(sub, bins=range(0, vmax + 2), edgecolor="black", linewidth=0.3)
            ax.set_title(f"{title} — {cfg}", fontsize=9)
            ax.set_xlabel(col, fontsize=8)
            if j == 0:
                ax.set_ylabel("count")
    fig.suptitle("Semantic signals: visible to our spans, invisible to perf/RAPL")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


def plot_4(df: pd.DataFrame, out: Path, base: str, compare: str) -> None:
    """Per-stage latency ratio compare/base on a log-scale bar.

    Only applicable when both configs have runs on the same set of query_ids.
    Ratios are computed from per-query means (each stage sum).
    """
    stages = [
        ("tool_search_ms", "tool.search"),
        ("tool_fetch_ms", "tool.fetch"),
        ("tool_summarize_ms", "tool.summarize"),
        ("llm_time_ms", "llm.generate"),
        ("framework_overhead_ms", "overhead"),
        # active_latency_ms excludes inter-turn rate-limit pauses;
        # use this rather than total_latency_ms so pacing doesn't
        # distort the cross-language comparison.
        ("active_latency_ms", "active (no pauses)"),
    ]

    base_df = df[df["config"] == base]
    cmp_df = df[df["config"] == compare]
    if base_df.empty or cmp_df.empty:
        print(f"plot_4: need both {base} and {compare}; skipping")
        return

    common_qids = sorted(set(base_df["query_id"]) & set(cmp_df["query_id"]))
    if not common_qids:
        print("plot_4: no overlapping query_ids between configs; skipping")
        return

    base_df = base_df[base_df["query_id"].isin(common_qids)].sort_values("query_id")
    cmp_df = cmp_df[cmp_df["query_id"].isin(common_qids)].sort_values("query_id")

    ratios: list[float] = []
    labels: list[str] = []
    for col, label in stages:
        b = float(base_df[col].mean())
        c = float(cmp_df[col].mean())
        if b <= 0 or c <= 0:
            ratios.append(float("nan"))
        else:
            ratios.append(c / b)
        labels.append(label)

    fig, ax = plt.subplots(figsize=(7, 4))
    xs = np.arange(len(labels))
    colors = ["#4477cc" if r and r < 1 else "#D86CA2" for r in ratios]
    ax.bar(xs, ratios, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_yscale("log")
    ax.axhline(1.0, color="black", linestyle="--", linewidth=0.8)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel(f"mean ratio  {compare} / {base}  (log)")
    ax.set_title(f"Per-stage latency ratio: {compare} vs {base} ({len(common_qids)} queries)")
    for x, r in zip(xs, ratios):
        if r and not np.isnan(r):
            ax.text(x, r, f"{r:.2f}x", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


def cli() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--traces-root", type=Path, default=Path("benchmark/traces"))
    ap.add_argument("--out", type=Path, default=Path("benchmark/results"))
    ap.add_argument("--configs", nargs="+", default=["py", "go"])
    ap.add_argument("--base", default="py", help="plot_4 baseline config")
    ap.add_argument("--compare", default="go", help="plot_4 compare config")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    dirs = {c: args.traces_root / c for c in args.configs}
    df = build_df(dirs)
    if df.empty:
        print("No traces found; nothing to plot.")
        return
    plot_2(df, args.out / "plot_2_configs.png", args.configs)
    plot_3(df, args.out / "plot_3_semantic_signals.png", args.configs)
    plot_4(df, args.out / "plot_4_stage_ratio.png", args.base, args.compare)


if __name__ == "__main__":
    cli()
