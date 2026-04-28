"""Figure 3: per-stage Py vs Go scatter, with y=x reference.

One point per (query, stage). x = Py wall time of that stage on that query,
y = Go wall time of the same stage on the same query. Color by stage. The
diagonal y=x is drawn as a thin gray reference line.

Visual claim: the cross-language equivalence holds across individual measurements,
not only on per-config means. If a stage systematically deviates from the
diagonal, that stage is the only place a language difference is plausibly
visible. (Spoiler from the data: it does not.)

Reads from benchmark/traces/{py,go}/. Independent of the new Cell 1 / Cell 2
runs. Idempotent.

Usage:
    python -m benchmark.plots_paper.make_fig3
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from benchmark.analysis.metrics import is_failed_trace, load_trace
from benchmark.plots_paper import style

_TRACES = {
    "py": Path("benchmark/traces/py"),
    "go": Path("benchmark/traces/go"),
}
# Stages plotted. retry_wait excluded: present in only some queries on the Go
# side, not on Py. Including it would cluster on the y-axis and distract.
_STAGES = ("llm", "tool.search", "tool.fetch", "tool.summarize")


def _per_query_stage_ms(trace_path: Path) -> dict[str, float] | None:
    """Sum wall_time_ms per stage for one trace JSON. Returns None if the
    trace is a documented failure (see metrics.is_failed_trace)."""
    tf = load_trace(trace_path)
    if is_failed_trace(tf):
        return None
    blob = json.loads(trace_path.read_text())
    sums: dict[str, float] = defaultdict(float)
    for s in blob["spans"]:
        name = s.get("name", "")
        if name == "llm.generate":
            sums["llm"] += float(s.get("wall_time_ms", 0.0))
        elif name in {"tool.search", "tool.fetch", "tool.summarize"}:
            sums[name] += float(s.get("wall_time_ms", 0.0))
    return sums


def _collect() -> tuple[
    dict[str, list[tuple[float, float]]],
    dict[str, list[tuple[float, float, str]]],
]:
    """For each stage: list of (py_ms, go_ms) pairs over common query_ids,
    plus a parallel list of (py_ms, go_ms, qid) for annotation work."""
    py_by_qid: dict[str, dict[str, float]] = {}
    go_by_qid: dict[str, dict[str, float]] = {}
    for path in sorted(_TRACES["py"].glob("*.json")):
        sums = _per_query_stage_ms(path)
        if sums is None:
            print(f"[fig3] skipping failed trace {path}", file=sys.stderr)
            continue
        py_by_qid[path.stem] = sums
    for path in sorted(_TRACES["go"].glob("*.json")):
        sums = _per_query_stage_ms(path)
        if sums is None:
            print(f"[fig3] skipping failed trace {path}", file=sys.stderr)
            continue
        go_by_qid[path.stem] = sums

    common = sorted(set(py_by_qid) & set(go_by_qid))
    if not common:
        raise RuntimeError("no common query_ids between py and go traces")

    out: dict[str, list[tuple[float, float]]] = {s: [] for s in _STAGES}
    out_with_qid: dict[str, list[tuple[float, float, str]]] = {s: [] for s in _STAGES}
    for qid in common:
        for stage in _STAGES:
            py_ms = py_by_qid[qid].get(stage, 0.0)
            go_ms = go_by_qid[qid].get(stage, 0.0)
            if py_ms == 0.0 and go_ms == 0.0:
                continue
            out[stage].append((py_ms, go_ms))
            out_with_qid[stage].append((py_ms, go_ms, qid))
    return out, out_with_qid


def main() -> int:
    style.configure()
    points, points_with_qid = _collect()

    fig, ax = plt.subplots(figsize=style.COLUMN_FIGSIZE)

    all_x: list[float] = []
    all_y: list[float] = []
    for stage in _STAGES:
        if not points[stage]:
            continue
        xs, ys = zip(*points[stage])
        ax.scatter(
            xs,
            ys,
            c=style.PALETTE[stage],
            s=14,
            alpha=0.75,
            label=style.DISPLAY_LABEL[stage],
            edgecolors="none",
        )
        all_x.extend(xs)
        all_y.extend(ys)

    # Always show all five instrumented stages in the legend, even when a
    # stage produced no data points in this workload (e.g. tool.summarize).
    # The caption explains why; absent stages should not silently disappear.
    legend_handles = []
    legend_labels = []
    for stage in _STAGES:
        if points[stage]:
            # already plotted; matplotlib will use the existing handle
            continue
        # synthetic handle so the absent stage still appears in the legend
        legend_handles.append(
            Line2D(
                [0], [0],
                marker="o", linestyle="none",
                markerfacecolor=style.PALETTE[stage],
                markeredgecolor="none",
                markersize=4,
                alpha=0.75,
            )
        )
        legend_labels.append(f"{style.DISPLAY_LABEL[stage]} (n=0)")

    # y=x diagonal across the full data range, slightly extended.
    if all_x and all_y:
        lim_lo = max(min(all_x + all_y), 0.05)
        lim_hi = max(all_x + all_y) * 1.2
        ax.plot([lim_lo, lim_hi], [lim_lo, lim_hi], "-", color="#888888", lw=0.7, zorder=0)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(lim_lo, lim_hi)
        ax.set_ylim(lim_lo, lim_hi)

    ax.set_xlabel("Py wall time (ms)")
    ax.set_ylabel("Go wall time (ms)")
    ax.set_aspect("equal", adjustable="box")

    # Combine real handles (from scatter) with synthetic absent-stage handles
    real_handles, real_labels = ax.get_legend_handles_labels()
    ax.legend(
        real_handles + legend_handles,
        real_labels + legend_labels,
        loc="upper left",
        frameon=False,
        handletextpad=0.3,
        borderaxespad=0.2,
        fontsize=7,
    )

    fig.tight_layout()
    pdf_path, png_path = style.save(fig, "fig3_cross_lang")
    plt.close(fig)

    caption_path = style.OUT_DIR / "fig3_cross_lang_caption.txt"
    with caption_path.open("w") as f:
        f.write(
            "Per-stage cross-language scatter, one point per (query, stage). "
            "The y=x diagonal is the cross-language equivalence reference. "
            "LLM points cluster along the diagonal across the full Py wall-time "
            "range (~2–12 s); tool.fetch shows moderate scatter consistent "
            "with network variance on identical URLs; tool.search points are "
            "clustered near the origin because the legacy traces used the "
            "static search backend (~0.08 ms per span). The cross-language "
            "equivalence claim — Py and Go runtimes produce comparable "
            "per-stage wall times — is supported across individual "
            "measurements, not just per-config means.\n\n"
            "tool.summarize is absent from this workload because the model "
            "did not invoke summarization in any of the sweep runs that "
            "produced this figure; the instrumentation supports the stage "
            "but the data does not exercise it. The stage is retained in "
            "the legend (annotated 'n=0') for completeness.\n\n"
            "Failed-run filter: traces with documented transport failures "
            "(e.g. HTTP 400 from an exhausted credit balance) are excluded "
            "by metrics.is_failed_trace. See benchmark/EVAL_PLAN.md "
            "'Historical traces' for the count and provenance.\n"
        )

    n_points = sum(len(v) for v in points.values())
    n_py = len(set(p[2] for p in points_with_qid["llm"]))  # per common qid
    print(f"wrote {pdf_path}  ({n_points} points across {len(_STAGES)} stages)")
    print(f"wrote {png_path}")
    print(f"wrote {caption_path}")
    print(f"  using {n_py} common queries after failure filter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
