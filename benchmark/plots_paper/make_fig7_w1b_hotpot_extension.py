"""Figure 7 — W1b extension: P50 summarize vs batch size on FreshQA vs HotpotQA.

The point of this figure is to test the W1b "attenuated GIL signal because
LexRank on small pages is too cheap" hypothesis directly. If the hypothesis
were correct, a heavier workload (HotpotQA's Wikipedia pages, originally
characterized in W1c as ~7.4x per-call summarize cost vs FreshQA) should
produce a steeper P50 summarize curve as batch size grows.

Observed: the two curves grow at essentially the same rate (1.55x HotpotQA
vs 1.59x FreshQA from b=1 to b=64). The hypothesis is not supported by the
data on this workload — and a follow-up diagnostic revealed that W1c's
"7.4x per-call" was a mean-vs-median artifact, so the workload in this
extension was not actually substantially heavier at the typical query.

Style follows fig 5 / fig 6: serif font, light dashed grid, log2 x-axis on
batch size, marker per language convention from fig 5 (circles for
ThreadPoolExecutor Python), legend below.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt

from benchmark.plots_paper import style

_BATCH_SIZES = (1, 4, 16, 64)
_DATASETS = (
    ("FreshQA-20",  "cell_concurrent_py_b{b}",        "#0072B2", "o"),
    ("HotpotQA-20", "cell_concurrent_py_hotpot_b{b}", "#CC79A7", "s"),
)


def _summarize_per_query(cell_dir: Path) -> list[float]:
    """Sum of tool.summarize wall_time_ms per query in a cell."""
    out = []
    for f in sorted(cell_dir.glob("*.json")):
        blob = json.loads(f.read_text())
        out.append(sum(sp["wall_time_ms"] for sp in blob["spans"]
                       if sp["name"] == "tool.summarize"))
    return out


def main() -> int:
    style.configure()

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8.0, 3.4))

    # Collect data
    series: dict[str, dict[str, list[float]]] = {}
    for label, tmpl, color, marker in _DATASETS:
        per_b: list[tuple[int, float, float]] = []  # (b, p50, mean)
        for b in _BATCH_SIZES:
            cdir = Path("benchmark/results") / tmpl.format(b=b)
            data = _summarize_per_query(cdir)
            if not data:
                continue
            per_b.append((b, statistics.median(data), sum(data) / len(data)))
        series[label] = {
            "color": color,
            "marker": marker,
            "b": [r[0] for r in per_b],
            "p50": [r[1] for r in per_b],
            "mean": [r[2] for r in per_b],
        }

    # Panel A — P50
    for i, (label, s) in enumerate(series.items()):
        axA.plot(s["b"], s["p50"], marker=s["marker"], color=s["color"],
                 linewidth=1.5, markersize=5, label=label)
    # Combined growth-ratio annotation, one row per dataset, anchored
    # in axes coordinates to avoid the two ~320 ms points overlapping.
    growth_lines = []
    for label, s in series.items():
        if len(s["p50"]) >= 2 and s["p50"][0] > 0:
            ratio = s["p50"][-1] / s["p50"][0]
            growth_lines.append((label, ratio, s["color"]))
    txt_y = 0.95
    axA.text(0.97, txt_y, "P50 growth, b=1 -> b=64:",
             transform=axA.transAxes, ha="right", va="top",
             fontsize=7, color="#444444")
    for j, (lab, r, col) in enumerate(growth_lines):
        axA.text(0.97, txt_y - 0.07 - 0.06 * j,
                 f"{lab}: x{r:.2f}",
                 transform=axA.transAxes, ha="right", va="top",
                 fontsize=7.5, color=col, fontweight="bold")
    axA.set_xscale("log", base=2)
    axA.set_xticks(list(_BATCH_SIZES))
    axA.set_xticklabels([str(b) for b in _BATCH_SIZES])
    axA.set_xlabel("concurrent batch size")
    axA.set_ylabel("P50 tool.summarize per query (ms)")
    axA.set_title("(A) P50 summarize — typical query")
    axA.set_ylim(bottom=0)

    # Panel B — Mean (right-tail-sensitive)
    for label, s in series.items():
        axB.plot(s["b"], s["mean"], marker=s["marker"], color=s["color"],
                 linewidth=1.5, markersize=5, label=label)
    axB.set_xscale("log", base=2)
    axB.set_xticks(list(_BATCH_SIZES))
    axB.set_xticklabels([str(b) for b in _BATCH_SIZES])
    axB.set_xlabel("concurrent batch size")
    axB.set_ylabel("mean tool.summarize per query (ms)")
    axB.set_title("(B) Mean summarize — sensitive to outlier pages")
    axB.set_ylim(bottom=0)

    # Single shared legend below
    handles, labels = axA.get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="lower center",
        ncol=len(labels),
        bbox_to_anchor=(0.5, -0.02),
        frameon=False,
        fontsize=8,
        columnspacing=1.4,
        handletextpad=0.4,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))

    pdf_path, png_path = style.save(fig, "fig7_w1b_hotpot_extension")
    plt.close(fig)

    cap = style.OUT_DIR / "fig7_w1b_hotpot_extension_caption.txt"
    fr = series["FreshQA-20"]
    hp = series["HotpotQA-20"]
    fr_growth = fr["p50"][-1] / fr["p50"][0]
    hp_growth = hp["p50"][-1] / hp["p50"][0]
    with cap.open("w") as f:
        f.write(
            "Figure 7 — W1b extension. Direct test of the W1b explanation that "
            "Python's GIL signal on the FreshQA pipeline was attenuated because "
            "LexRank on small news-clip pages is too cheap to expose contention. "
            "Re-runs the Python concurrent sweep at batch sizes [1, 4, 16, 64] "
            "on HotpotQA-20 (Wikipedia pages, expected heavier per-call "
            "summarize work) and overlays the curves on the FreshQA baseline.\n\n"
            "Panel A (P50 summarize, the per-typical-query cost): the two "
            f"curves grow at essentially the same rate from b=1 to b=64 — "
            f"x{fr_growth:.2f} on FreshQA and x{hp_growth:.2f} on HotpotQA. "
            "Hypothesis NOT supported on this workload.\n\n"
            "Panel B (mean summarize, the right-tail-sensitive view): HotpotQA "
            "is much more variable because ~1 in 20 queries surfaces a large "
            "Wikipedia page that takes 5-22 seconds to LexRank, single-handedly "
            "inflating the mean. The HotpotQA mean dropped at b=64 because DDG "
            "returned different (smaller) URLs under heavy concurrent load — a "
            "workload-side effect, not a runtime-side one. The mean is therefore "
            "an unreliable comparison metric here; Panel A is the honest read.\n\n"
            "Implication: the W1c claim that HotpotQA pages cost +637% per "
            "summarize call (mean) was driven by these long-tail outliers. At "
            "the median, HotpotQA per-call summarize cost is only ~1.3x "
            "FreshQA's. So this extension did not actually run a meaningfully "
            "heavier workload at the typical query, and the GIL hypothesis "
            "remains untested at scale rather than refuted. To test it "
            "properly would require a workload whose median (not just its "
            "tail) is much heavier than FreshQA's.\n"
        )
    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
