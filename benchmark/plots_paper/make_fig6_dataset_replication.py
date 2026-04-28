"""Figure 6 — Experiment B (Workload 1c): cost-breakdown shape across datasets.

Two panels comparing the Haiku 4.5 + pipeline cell run on FreshQA-20 vs
HotpotQA-20 (the same pipeline architecture, the same model, the same
instrumentation — only the query set changes):

  Panel A (left) — Percentage breakdown.
    Two stacked bars side by side, normalized to 100% of active wall time.
    Stages: LLM, Tool: search, Tool: fetch, Tool: summarize.
    Reads as: "the cost-breakdown shape generalizes" — both bars sit
    near 84-87% tool / 13-16% LLM. The architecture-vs-model finding from
    EVAL_LOG.md (Haiku -> Opus on pipeline = +0.1 pp shift; Haiku -> Opus
    on agentic = +9.3 pp shift) rests on a stable foundation.

  Panel B (right) — Absolute MEDIAN milliseconds breakdown (typical query).
    Same data as Panel A but as median per-query wall time per stage,
    grouped bar chart with one group per stage. Median (not mean) is the
    honest comparison metric — the W1b extension showed that mean is
    dominated by tail outliers (typically one ~22-second pathological
    Wikipedia page per HotpotQA batch). At the typical query, the per-
    stage costs are roughly comparable across datasets; the dramatic
    "+637% summarize" claim from the original mean-based version of this
    figure was a tail-outlier artifact (see EVAL_LOG.md / W1b extension).

Why two panels: Panel A shows that the cost-breakdown SHAPE generalizes
across datasets. Panel B shows that even the absolute per-stage MEDIANS
are roughly comparable across datasets — the architecture-vs-model
finding from EVAL_LOG rests on this stability at both views.

Data sources:
  benchmark/results/cell_haiku_pipeline/         (FreshQA-20, n=20)
  benchmark/results/cell_haiku_pipeline_hotpot/  (HotpotQA-20, n=20)
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from benchmark.analysis.metrics import load_trace, per_query_row
from benchmark.plots_paper import style

_CELLS = (
    ("freshqa",  Path("benchmark/results/cell_haiku_pipeline"),         "FreshQA-20"),
    ("hotpotqa", Path("benchmark/results/cell_haiku_pipeline_hotpot"),  "HotpotQA-20"),
)
# Panel ordering: bottom-to-top in Panel A; left-to-right in Panel B.
_STAGES = ("llm", "tool.search", "tool.fetch", "tool.summarize")


def _per_cell_metrics(cell_dir: Path) -> dict:
    """Return {stage: [per_query_share]} and {stage: [per_query_ms]} plus n
    and median active wall ms for the annotation above each percentage bar.
    """
    if not cell_dir.exists():
        raise FileNotFoundError(f"cell directory not found: {cell_dir}")
    rows = [per_query_row(load_trace(p)) for p in sorted(cell_dir.glob("*.json"))]
    if not rows:
        raise RuntimeError(f"no trace JSONs in {cell_dir}")
    shares: dict[str, list[float]] = {s: [] for s in _STAGES}
    abs_ms: dict[str, list[float]] = {s: [] for s in _STAGES}
    for r in rows:
        active = r["active_latency_ms"] or 1.0
        shares["llm"].append(r["llm_time_ms"] / active)
        shares["tool.search"].append(r["tool_search_ms"] / active)
        shares["tool.fetch"].append(r["tool_fetch_ms"] / active)
        shares["tool.summarize"].append(r["tool_summarize_ms"] / active)
        abs_ms["llm"].append(r["llm_time_ms"])
        abs_ms["tool.search"].append(r["tool_search_ms"])
        abs_ms["tool.fetch"].append(r["tool_fetch_ms"])
        abs_ms["tool.summarize"].append(r["tool_summarize_ms"])
    return {
        "n": len(rows),
        "shares": shares,
        "abs_ms": abs_ms,
        "median_active_ms": statistics.median(r["active_latency_ms"] for r in rows),
    }


def main() -> int:
    style.configure()
    cells = []
    for key, cdir, display in _CELLS:
        cells.append((key, display, _per_cell_metrics(cdir)))

    # ---- Plot ---------------------------------------------------------------
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8.0, 3.6))

    # Panel A — percentage stacked bars
    x_pct = np.arange(len(cells))
    width_pct = 0.55
    bottom = np.zeros(len(cells))
    for stage in _STAGES:
        heights = np.array([
            100.0 * sum(c[2]["shares"][stage]) / c[2]["n"] for c in cells
        ])
        axA.bar(
            x_pct,
            heights,
            width_pct,
            bottom=bottom,
            color=style.PALETTE[stage],
            label=style.DISPLAY_LABEL[stage],
            edgecolor="white",
            linewidth=0.4,
        )
        # Inline percentage label inside each segment if it's tall enough
        for xi, h in zip(x_pct, heights):
            if h >= 5.0:
                axA.text(
                    xi,
                    bottom[xi] + h / 2,
                    f"{h:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=6.5,
                    color="white" if stage in ("llm", "tool.fetch") else "#222222",
                )
        bottom += heights
    # Annotation above each bar: median active wall + n
    for xi, c in enumerate(cells):
        axA.text(
            xi,
            102,
            f"median {c[2]['median_active_ms']/1000:.1f} s\n(n={c[2]['n']})",
            ha="center",
            va="bottom",
            fontsize=6.5,
        )
    axA.set_xticks(x_pct)
    axA.set_xticklabels([c[1] for c in cells])
    axA.set_ylabel("share of active wall time (%)")
    axA.set_ylim(0, 118)
    axA.set_yticks([0, 25, 50, 75, 100])
    axA.set_title("(A) Cost-breakdown shape generalizes")

    # Panel B — absolute MEDIAN milliseconds per query, grouped by stage.
    # Median is the honest comparison metric here: the W1b extension showed
    # that mean is dominated by tail outliers (typically one large
    # Wikipedia page per HotpotQA batch that LexRank chokes on for
    # 5-22 seconds). See EVAL_LOG.md / W1b extension for the artifact.
    n_groups = len(_STAGES)
    n_cells = len(cells)
    bar_width = 0.36
    x_abs = np.arange(n_groups)
    medians_per_cell: list[list[float]] = []
    for ci, c in enumerate(cells):
        offsets = bar_width * (ci - (n_cells - 1) / 2)
        medians = [statistics.median(c[2]["abs_ms"][s]) for s in _STAGES]
        medians_per_cell.append(medians)
        face = "#0072B2" if c[0] == "freshqa" else "#E69F00"
        axB.bar(
            x_abs + offsets,
            medians,
            bar_width,
            color=face,
            edgecolor="white",
            linewidth=0.4,
            label=c[1],
        )
        # Print absolute median ms on top of each bar
        for xi, m in zip(x_abs, medians):
            axB.text(
                xi + offsets,
                m + 200,
                f"{m:,.0f}",
                ha="center",
                va="bottom",
                fontsize=6.5,
            )
    # Per-stage delta annotation on the HotpotQA bars, using MEDIAN ratios.
    fr_med = {s: statistics.median(cells[0][2]["abs_ms"][s]) for s in _STAGES}
    hp_med = {s: statistics.median(cells[1][2]["abs_ms"][s]) for s in _STAGES}
    for xi, s in enumerate(_STAGES):
        if fr_med[s] == 0:
            continue
        delta = (hp_med[s] - fr_med[s]) / fr_med[s] * 100.0
        sign = "+" if delta >= 0 else ""
        axB.text(
            xi + bar_width * 0.5,
            hp_med[s] + 700,
            f"{sign}{delta:.0f}%",
            ha="center",
            va="bottom",
            fontsize=7,
            fontweight="bold",
            color="#222222",
        )
    axB.set_xticks(x_abs)
    axB.set_xticklabels([style.DISPLAY_LABEL[s] for s in _STAGES], rotation=15, ha="right")
    axB.set_ylabel("median per-query wall time (ms)")
    # Auto-fit y-limit with headroom for the delta annotation row.
    ymax = max(max(row) for row in medians_per_cell)
    axB.set_ylim(top=ymax * 1.30)
    axB.set_title("(B) Absolute magnitudes — typical query (median)")
    axB.legend(loc="upper right", fontsize=8, frameon=False)

    # Shared figure-level legend for the percentage panel (A) below it
    axA.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.10),
        ncol=2,
        frameon=False,
        fontsize=7,
        columnspacing=1.2,
        handletextpad=0.4,
    )

    fig.tight_layout()
    pdf_path, png_path = style.save(fig, "fig6_dataset_replication")
    plt.close(fig)

    # ---- Caption -----------------------------------------------------------
    cap = style.OUT_DIR / "fig6_dataset_replication_caption.txt"
    fr_share_llm = 100.0 * sum(cells[0][2]["shares"]["llm"]) / cells[0][2]["n"]
    hp_share_llm = 100.0 * sum(cells[1][2]["shares"]["llm"]) / cells[1][2]["n"]
    fr_share_tool = 100.0 * sum(
        sum(cells[0][2]["shares"][s]) for s in ("tool.search", "tool.fetch", "tool.summarize")
    ) / cells[0][2]["n"]
    hp_share_tool = 100.0 * sum(
        sum(cells[1][2]["shares"][s]) for s in ("tool.search", "tool.fetch", "tool.summarize")
    ) / cells[1][2]["n"]
    # Per-stage median deltas for the caption text (matching what the
    # figure annotates above the HotpotQA bars).
    delta_pct = {
        s: (hp_med[s] - fr_med[s]) / fr_med[s] * 100.0 if fr_med[s] else float("nan")
        for s in _STAGES
    }
    with cap.open("w") as f:
        f.write(
            "Figure 6 — Experiment B / Workload 1c. Cost-breakdown comparison "
            "of the Haiku 4.5 + hardcoded pipeline cell on two QA benchmarks: "
            "FreshQA-20 (mixed freshness levels: never/slow/fast-changing) and "
            "HotpotQA-20 (3 buckets: 7 bridge-entity + 7 comparison-entity + "
            "6 comparison-yesno; selection_seed=42).\n\n"
            "Caveat — read first: HotpotQA dev distractor only contains "
            "level=hard examples (easy/medium are train-only), so the HotpotQA "
            "cell covers only multi-hop hard questions. FreshQA-20 covers a "
            "broader difficulty range. The cells are comparable for the "
            "question 'does the cost-breakdown shape generalize?' but they "
            "cover DIFFERENT difficulty distributions, not the same one.\n\n"
            "Panel A — stacked percentage breakdown (mean shares across "
            "queries). Both cells sit in the same regime: tool-dominant by a "
            f"wide margin (FreshQA {fr_share_tool:.1f}%, HotpotQA "
            f"{hp_share_tool:.1f}%) with LLM share small (FreshQA "
            f"{fr_share_llm:.1f}%, HotpotQA {hp_share_llm:.1f}%). The "
            "architecture-vs-model finding from EVAL_LOG (the +0.1 pp "
            "Haiku->Opus shift on pipeline vs the +9.3 pp shift on the "
            "agentic loop) rests on this stable foundation: the cost SHAPE "
            "is dataset-independent at the architecture level.\n\n"
            "Panel B — absolute MEDIAN per-query wall time, grouped by stage. "
            "Median (not mean) is the honest comparison metric here: the W1b "
            "extension (see EVAL_LOG.md / Workload 1b extension) showed that "
            "mean is dominated by tail outliers — typically one ~22-second "
            "pathological Wikipedia page per HotpotQA batch on which LexRank "
            "chokes. At the typical query the per-stage MEDIANS are roughly "
            f"comparable across datasets: LLM {delta_pct['llm']:+.0f}%, "
            f"search {delta_pct['tool.search']:+.0f}%, fetch "
            f"{delta_pct['tool.fetch']:+.0f}%, summarize "
            f"{delta_pct['tool.summarize']:+.0f}%. An earlier version of this "
            "figure (before the W1b extension) used MEAN per-query wall time "
            "and reported a +637% summarize jump and a 7.4x per-call summarize "
            "claim; both were tail-outlier artifacts and have been corrected.\n\n"
            "Implication for Workload 1b: the W1b extension was run on "
            "HotpotQA-20 motivated by what was then thought to be a heavier "
            "summarize workload. The test came back inconclusive — P50 "
            "summarize growth from b=1 to b=64 was x1.55 on HotpotQA vs x1.59 "
            "on FreshQA, essentially identical, because the workloads were "
            "not actually different at the typical query (this median view "
            "shows why). The W1b 'lightweight LexRank' explanation is now "
            "uncertain rather than supported. See EVAL_LOG.md for the full "
            "amendment trail.\n\n"
            "Token budget across datasets is essentially flat: input tokens "
            "drop -14% (FreshQA queries are slightly longer than HotpotQA's), "
            "output tokens are within +1.4%. The LLM stage behaves the same "
            "on both datasets, which makes the architecture-vs-model finding "
            "more robust: the variance is on the tool side, but even there "
            "the typical query is comparable across the two benchmarks.\n"
        )

    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
