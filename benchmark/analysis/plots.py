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


def plot_5_orchestration_throughput(
    sweep_index_path: Path,
    out: Path,
) -> None:
    """Two-panel figure for the Phase-2 concurrency sweep.

    Reads benchmark/output/sweep/sweep_index.json (the on-disk pointer file
    written by the sweep runner) and produces:
      - Top:    orchestration_ms p50 (and p90 dashed) vs N, one line per mode.
      - Bottom: throughput_qps mean across runs vs N, one line per mode.

    Sparse data is fine — early in Phase 2 we render with as few as 2 points
    per line. The plot is for sanity-checking the sweep, not the final figure.
    """
    import json as _json

    if not sweep_index_path.exists():
        raise FileNotFoundError(f"sweep_index.json not found at {sweep_index_path}")
    idx = _json.loads(sweep_index_path.read_text())
    points = idx.get("points", [])
    if not points:
        raise RuntimeError(f"{sweep_index_path}: no points to plot")

    # Group by mode -> list of (N, summary)
    by_mode: dict[str, list[tuple[int, dict]]] = {}
    for p in points:
        try:
            sm = _json.loads(Path(p["summary_path"]).read_text())
        except (FileNotFoundError, _json.JSONDecodeError):
            continue
        by_mode.setdefault(p["mode"], []).append((int(p["N"]), sm))
    for mode in by_mode:
        by_mode[mode].sort(key=lambda r: r[0])

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(7, 7), sharex=True)
    mode_color = {"py-mt": "#D86CA2", "py-mp": "#4477cc", "go": "#2A8B5A"}

    for mode, rows in sorted(by_mode.items()):
        ns = [r[0] for r in rows]
        p50 = [r[1].get("orchestration_ms", {}).get("p50", 0.0) for r in rows]
        p90 = [r[1].get("orchestration_ms", {}).get("p90", 0.0) for r in rows]
        # Throughput: mean across runs.
        thr = []
        for _, sm in rows:
            runs = sm.get("runs", [])
            qps = [r.get("throughput_qps", 0.0) for r in runs]
            thr.append(sum(qps) / len(qps) if qps else 0.0)

        c = mode_color.get(mode, "#666666")
        ax_top.plot(ns, p50, marker="o", color=c, label=f"{mode} p50")
        ax_top.plot(ns, p90, marker="x", color=c, linestyle="--", alpha=0.7, label=f"{mode} p90")
        ax_bot.plot(ns, thr, marker="o", color=c, label=mode)

    ax_top.set_ylabel("orchestration_ms")
    ax_top.set_title("Per-query orchestration time vs concurrency")
    ax_top.set_xscale("log", base=2)
    ax_top.set_yscale("log")
    ax_top.grid(True, which="both", alpha=0.3)
    ax_top.legend(fontsize=8, ncol=3, loc="upper left")

    ax_bot.set_ylabel("throughput (queries/sec)")
    ax_bot.set_xlabel("N (concurrency)")
    ax_bot.set_title("Throughput vs concurrency (mean over runs)")
    ax_bot.set_xscale("log", base=2)
    ax_bot.grid(True, which="both", alpha=0.3)
    ax_bot.legend(fontsize=9, loc="upper left")

    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


def plot_6_latency_decomposition(
    sweep_index_path: Path,
    out: Path,
) -> None:
    """Where the wall-clock goes per query, per (mode, N).

    For each point in the sweep, decompose median per-query gross_wallclock_ms
    into:
      - llm_ms       (sum of successful llm.generate spans, median per query)
      - tool_ms      (sum of tool.* spans, median per query)
      - gap_ms       (root.wall - sum of children, median per query)

    Plotted as a grouped stacked-bar chart: one cluster per N, three bars
    per cluster (one per mode). This is the "where is the bottleneck"
    diagnostic that pairs with plot_5.
    """
    import json as _json
    import statistics as _stat

    if not sweep_index_path.exists():
        raise FileNotFoundError(sweep_index_path)
    idx = _json.loads(sweep_index_path.read_text())
    points = idx.get("points", [])
    if not points:
        raise RuntimeError(f"{sweep_index_path}: no points")

    # decomp[mode][N] = (llm, tool, gap) medians (ms)
    decomp: dict[str, dict[int, tuple[float, float, float]]] = {}

    for p in points:
        sm = _json.loads(Path(p["summary_path"]).read_text())
        mode, n = p["mode"], int(p["N"])
        # We have to dig into individual trace JSONs to get per-query
        # decomposition; the summary only carries pooled numbers.
        from benchmark.analysis.metrics import load_trace

        traces_root = Path(p["summary_path"]).parent / "traces"
        llms, tools, gaps = [], [], []
        for run_dir in sorted(traces_root.glob("run*")):
            for tp in sorted(run_dir.glob("*.json")):
                try:
                    tf = load_trace(tp)
                except Exception:
                    continue
                root = next((s for s in tf.spans if s.parent_id is None), None)
                if root is None:
                    continue
                tot = root.wall_time_ms
                tool_sum = sum(
                    s.wall_time_ms for s in tf.spans if s.kind == "tool" and s is not root
                )
                llm_sum = sum(
                    s.wall_time_ms
                    for s in tf.spans
                    if s.kind == "llm"
                    and s.name == "llm.generate"
                    and not bool(s.attrs.get("llm.rate_limited", False))
                )
                gap = max(tot - tool_sum - llm_sum, 0.0)
                llms.append(llm_sum)
                tools.append(tool_sum)
                gaps.append(gap)
        if not llms:
            continue
        decomp.setdefault(mode, {})[n] = (
            _stat.median(llms),
            _stat.median(tools),
            _stat.median(gaps),
        )
        # Suppress unused-warning on sm in environments where the linter complains.
        _ = sm

    if not decomp:
        raise RuntimeError("plot_6: no decomposed data")

    modes = ["py-mt", "py-mp", "go"]
    all_ns = sorted({n for m in decomp.values() for n in m.keys()})
    mode_color = {"py-mt": "#D86CA2", "py-mp": "#4477cc", "go": "#2A8B5A"}
    seg_alpha = {"llm": 1.0, "tool": 0.7, "gap": 0.35}
    seg_hatch = {"llm": None, "tool": "//", "gap": "xx"}

    fig, ax = plt.subplots(1, 1, figsize=(11, 6))
    bar_w = 0.25
    n_modes = len(modes)
    for mi, mode in enumerate(modes):
        if mode not in decomp:
            continue
        xs = []
        bottoms_llm = []
        bottoms_tool = []
        gap_heights = []
        llm_heights = []
        tool_heights = []
        for ni, n in enumerate(all_ns):
            if n not in decomp[mode]:
                continue
            llm_h, tool_h, gap_h = decomp[mode][n]
            x = ni + (mi - (n_modes - 1) / 2) * bar_w
            xs.append(x)
            llm_heights.append(llm_h)
            tool_heights.append(tool_h)
            gap_heights.append(gap_h)
            bottoms_llm.append(0)
            bottoms_tool.append(llm_h)
        c = mode_color[mode]
        ax.bar(xs, llm_heights, bar_w, color=c, alpha=seg_alpha["llm"],
               label=f"{mode} llm" if mi == 0 else None)
        ax.bar(xs, tool_heights, bar_w, bottom=bottoms_tool, color=c,
               alpha=seg_alpha["tool"], hatch=seg_hatch["tool"],
               label=f"{mode} tool" if mi == 0 else None)
        ax.bar(xs,
               gap_heights, bar_w,
               bottom=[a + b for a, b in zip(bottoms_tool, tool_heights)],
               color=c, alpha=seg_alpha["gap"], hatch=seg_hatch["gap"],
               label=f"{mode} gap" if mi == 0 else None)
        # Mode label on top of each cluster's right-most bar
        for x, llm_h, tool_h, gap_h in zip(xs, llm_heights, tool_heights, gap_heights):
            ax.text(x, llm_h + tool_h + gap_h, mode[:2],
                    ha="center", va="bottom", fontsize=7, color=c)

    # legend explaining segments only (modes are encoded in color + label)
    from matplotlib.patches import Patch

    legend_segs = [
        Patch(facecolor="#888", alpha=seg_alpha["llm"], label="llm.generate"),
        Patch(facecolor="#888", alpha=seg_alpha["tool"], hatch=seg_hatch["tool"], label="tool.*"),
        Patch(facecolor="#888", alpha=seg_alpha["gap"], hatch=seg_hatch["gap"], label="gap (idle/glue)"),
    ]
    legend_modes = [
        Patch(facecolor=mode_color["py-mt"], label="py-mt"),
        Patch(facecolor=mode_color["py-mp"], label="py-mp"),
        Patch(facecolor=mode_color["go"], label="go"),
    ]
    ax.legend(handles=legend_segs + legend_modes, ncol=2, fontsize=9, loc="upper left")

    ax.set_xticks(range(len(all_ns)))
    ax.set_xticklabels([f"N={n}" for n in all_ns])
    ax.set_ylabel("median per-query wall_time_ms")
    ax.set_title("Where the wall-clock goes — per-query latency decomposition")
    ax.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


def plot_7_throughput_speedup(
    sweep_index_path: Path,
    out: Path,
) -> None:
    """Throughput speedup vs concurrency, normalized to N=1 baseline.

    For each mode, plots throughput(N) / throughput(N=1) vs N. The dashed
    line y=N is the ideal-linear-scaling reference. A mode that's strongly
    bottlenecked (e.g. by tool subprocess contention or GIL-bound glue)
    visibly diverges from the diagonal.
    """
    import json as _json

    if not sweep_index_path.exists():
        raise FileNotFoundError(sweep_index_path)
    idx = _json.loads(sweep_index_path.read_text())
    points = idx.get("points", [])
    if not points:
        raise RuntimeError(f"{sweep_index_path}: no points")

    by_mode: dict[str, list[tuple[int, float]]] = {}
    for p in points:
        sm = _json.loads(Path(p["summary_path"]).read_text())
        runs = sm.get("runs", [])
        if not runs:
            continue
        qps = sum(r.get("throughput_qps", 0.0) for r in runs) / len(runs)
        by_mode.setdefault(p["mode"], []).append((int(p["N"]), qps))
    for m in by_mode:
        by_mode[m].sort()

    fig, ax = plt.subplots(1, 1, figsize=(8, 6))
    mode_color = {"py-mt": "#D86CA2", "py-mp": "#4477cc", "go": "#2A8B5A"}

    max_n = 1
    for mode, rows in sorted(by_mode.items()):
        ns = [n for n, _ in rows]
        qpss = [q for _, q in rows]
        if not qpss or qpss[0] <= 0:
            continue
        speedup = [q / qpss[0] for q in qpss]
        max_n = max(max_n, max(ns))
        ax.plot(ns, speedup, marker="o", color=mode_color.get(mode, "#666"),
                label=f"{mode} (N=1 baseline = {qpss[0]:.3f} qps)")

    # ideal-linear reference
    ax.plot([1, max_n], [1, max_n], linestyle="--", color="#888", alpha=0.6,
            label="ideal linear (y = N)")

    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    ax.set_xlabel("N (concurrency)")
    ax.set_ylabel("throughput speedup vs N=1")
    ax.set_title("Concurrency scaling efficiency")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=9, loc="upper left")

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
    ap.add_argument("--sweep-index", type=Path,
                    default=Path("benchmark/output/sweep/sweep_index.json"),
                    help="sweep_index.json for plot_5_orchestration_throughput")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    dirs = {c: args.traces_root / c for c in args.configs}
    df = build_df(dirs)
    if not df.empty:
        plot_2(df, args.out / "plot_2_configs.png", args.configs)
        plot_3(df, args.out / "plot_3_semantic_signals.png", args.configs)
        plot_4(df, args.out / "plot_4_stage_ratio.png", args.base, args.compare)
    if args.sweep_index.exists():
        plot_5_orchestration_throughput(
            args.sweep_index,
            args.out / "plot_5_orchestration_throughput.png",
        )
    elif df.empty:
        print("No traces and no sweep index; nothing to plot.")


if __name__ == "__main__":
    cli()
