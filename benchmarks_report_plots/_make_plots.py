"""Generate the 6 hand-off PDFs for Anna's advisor (Jaime).

All figures share the rcParams set in `_setup_style`. Output goes to the
parent dir (benchmarks_report_plots/). PDFs are vector (text not rasterised).
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "benchmarks_report_plots"
OUT.mkdir(parents=True, exist_ok=True)

# Stage colors used consistently across fig1, fig1b, fig2, fig6 so the same
# semantic stage looks identical in every figure.
COLOR_LLM            = "#70AD47"  # green
COLOR_TOOL_NETWORK   = "#5B9BD5"  # blue (URL fetch / web)
COLOR_TOOL_COMPUTE   = "#ED7D31"  # orange (LexRank / RDKit / Bash / smiles_to_3d / calculator)
COLOR_TOOL_SECONDARY = "#FFC000"  # yellow (compute_descriptors / lookup_molecule and other named-but-secondary tools)
COLOR_ORCH           = "#A6A6A6"  # gray (orchestration gap, including rate-limit pauses)
COLOR_OTHER          = "#D9D9D9"  # light gray (uncategorised residual)


def _setup_style() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linestyle": "--",
        "savefig.bbox": "tight",
        "savefig.dpi": 300,
        "pdf.fonttype": 42,
    })


# ---- shared helpers ---------------------------------------------------------

def _load_traces(glob_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(glob_dir.glob("q*.json"))]


def _root(t: dict) -> dict:
    return next(s for s in t["spans"] if s["parent_id"] is None)


def _direct_child_walls(t: dict) -> dict[str, float]:
    """Sum wall_time_ms by name across direct children of root."""
    root = _root(t)
    out: dict[str, float] = {}
    for s in t["spans"]:
        if s.get("parent_id") == root["span_id"]:
            out[s["name"]] = out.get(s["name"], 0.0) + s["wall_time_ms"]
    return out


# =============================================================================
# PLOT 1 — fig1_freshqa_vs_raj.pdf
# =============================================================================

def plot_1_freshqa_vs_raj() -> None:
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8, 3.5))

    # ---- Panel A: schematic recreation of Raj et al. Fig 2(c) ----
    raj_stages = [
        ("URL fetch", 15, "#5B9BD5"),
        ("LexRank summarize", 55, "#ED7D31"),
        ("LLM inference", 30, "#70AD47"),
    ]
    bottom = 0.0
    for name, pct, color in raj_stages:
        axA.bar(0, pct, bottom=bottom, color=color, edgecolor="white",
                width=0.5, label=name)
        # annotate %
        axA.text(0, bottom + pct / 2, f"{name}\n{pct}%", ha="center", va="center",
                 fontsize=9, color="white" if name != "URL fetch" else "black")
        bottom += pct
    axA.set_xlim(-0.7, 0.7)
    axA.set_ylim(0, 100)
    axA.set_ylabel("% of wall time")
    axA.set_xticks([0])
    axA.set_xticklabels(["Raj et al.\n(vLLM + Llama-3-8B)"])
    axA.set_yticks([0, 25, 50, 75, 100])

    # ---- Panel B: real FreshQA Haiku 4.5 traces ----
    fq_dir = REPO / "benchmark" / "results" / "cell_haiku_agentic"
    traces = _load_traces(fq_dir)
    # Stages: URL fetch (search+fetch), LexRank summarize, LLM generate, orchestration
    fetch_walls = []
    summ_walls = []
    llm_walls = []
    orch_walls = []
    total_walls = []
    for t in traces:
        root = _root(t)
        total = root["wall_time_ms"]
        children = _direct_child_walls(t)
        fetch_ms = children.get("tool.fetch", 0.0) + children.get("tool.search", 0.0)
        summ_ms = children.get("tool.summarize", 0.0)
        llm_ms = children.get("llm.generate", 0.0)
        used = fetch_ms + summ_ms + llm_ms
        orch_ms = max(total - used, 0.0)
        fetch_walls.append(fetch_ms)
        summ_walls.append(summ_ms)
        llm_walls.append(llm_ms)
        orch_walls.append(orch_ms)
        total_walls.append(total)
    med = lambda xs: statistics.median(xs) if xs else 0.0
    fetch_med = med(fetch_walls)
    summ_med = med(summ_walls)
    llm_med = med(llm_walls)
    orch_med = med(orch_walls)
    total_med = fetch_med + summ_med + llm_med + orch_med

    bb_stages = [
        ("URL fetch", fetch_med, "#5B9BD5"),
        ("LexRank summarize", summ_med, "#ED7D31"),
        ("LLM generate", llm_med, "#70AD47"),
        ("Orchestration", orch_med, "#A6A6A6"),
    ]
    bottom = 0.0
    for name, ms, color in bb_stages:
        if ms <= 0:
            continue
        axB.bar(0, ms, bottom=bottom, color=color, edgecolor="white",
                width=0.5, label=name)
        pct = ms / total_med * 100 if total_med > 0 else 0
        axB.text(0, bottom + ms / 2, f"{name}\n{ms:.0f} ms ({pct:.0f}%)",
                 ha="center", va="center", fontsize=9,
                 color="white" if name != "URL fetch" else "black")
        bottom += ms
    axB.set_xlim(-0.7, 0.7)
    axB.set_ylim(0, total_med * 1.05)
    axB.set_ylabel("Median per-query wall time (ms)")
    axB.set_xticks([0])
    axB.set_xticklabels(["This thesis\n(Claude Haiku 4.5)"])

    fig.tight_layout()
    fig.savefig(OUT / "fig1_freshqa_vs_raj.pdf")
    plt.close(fig)
    print(f"[plot1] FreshQA medians: fetch={fetch_med:.0f}  summ={summ_med:.0f}  "
          f"llm={llm_med:.0f}  orch={orch_med:.0f}  total={total_med:.0f}")


# =============================================================================
# PLOT 2 — fig2_chemcrow_molecule_flip.pdf
# =============================================================================

def _chemcrow_n1_traces_by_label() -> dict[str, list[dict]]:
    """Pool ChemCrow py-mp_n1 traces (60 total) split by label."""
    cc_root = REPO / "benchmark" / "output" / "sweep" / "py-mp_n1" / "traces"
    out: dict[str, list[dict]] = {"medium": [], "heavy": []}
    for run_dir in sorted(cc_root.glob("run*")):
        if run_dir.is_dir():
            for t in _load_traces(run_dir):
                lab = t.get("label")
                if lab in out:
                    out[lab].append(t)
    return out


def plot_2_chemcrow_molecule_flip() -> None:
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8, 4))
    groups = _chemcrow_n1_traces_by_label()

    # Stage order (bottom→top) for panel A — full breakdown.
    STAGES_FULL = [
        ("tool.lookup_molecule",     "Lookup molecule",       COLOR_TOOL_SECONDARY),
        ("tool.smiles_to_3d",        "SMILES → 3D",           COLOR_TOOL_COMPUTE),
        ("tool.compute_descriptors", "Compute descriptors",   COLOR_TOOL_NETWORK),
        ("llm.generate",             "LLM generate",          COLOR_LLM),
        ("__other__",                "Orchestration / other", COLOR_ORCH),
    ]
    # Panel B: tools only (zoom).
    STAGES_TOOLS = [s for s in STAGES_FULL if s[0].startswith("tool.")]

    def medians(ts: list[dict], stages: list[tuple[str, str, str]]) -> dict[str, float]:
        per: dict[str, list[float]] = {s[0]: [] for s in stages}
        for t in ts:
            ch = _direct_child_walls(t)
            sum_known = 0.0
            for key, _, _ in stages:
                if key == "__other__":
                    continue
                v = ch.get(key, 0.0)
                per[key].append(v)
                sum_known += v
            if "__other__" in per:
                root = _root(t)
                per["__other__"].append(max(root["wall_time_ms"] - sum_known, 0.0))
        return {k: statistics.median(v) for k, v in per.items() if v}

    def draw_panel(ax, stages, title, annotate_pct: bool) -> None:
        pos = [0, 1]
        labels = ["Medium\nmolecules", "Heavy\nmolecules"]
        width = 0.55
        added_legend: set[str] = set()
        max_total = 0.0
        for i, (lab, ts) in enumerate([("medium", groups["medium"]),
                                        ("heavy",  groups["heavy"])]):
            med = medians(ts, stages)
            total = sum(med.values())
            max_total = max(max_total, total)
            bottom = 0.0
            for key, name, color in stages:
                v = med.get(key, 0.0)
                if v <= 0:
                    continue
                lab_kw = {"label": name} if name not in added_legend else {}
                added_legend.add(name)
                ax.bar(pos[i], v, bottom=bottom, color=color, edgecolor="white",
                       width=width, **lab_kw)
                if annotate_pct and total > 0:
                    pct = v / total * 100
                    if pct >= 4:
                        ax.text(pos[i], bottom + v / 2, f"{pct:.0f}%",
                                ha="center", va="center", fontsize=8, color="white")
                else:
                    # Panel B: annotate absolute ms inside each segment
                    if v >= max_total * 0.05:
                        ax.text(pos[i], bottom + v / 2, f"{v:.0f} ms",
                                ha="center", va="center", fontsize=8, color="white")
                bottom += v
            ax.text(pos[i], total + max_total * 0.02, f"{total:.0f} ms",
                    ha="center", va="bottom", fontsize=9)
        ax.set_xticks(pos)
        ax.set_xticklabels(labels)
        ax.set_ylabel("Median per-query wall time (ms)")
        ax.set_title(title, fontsize=10)
        ax.set_ylim(0, max_total * 1.12)

    draw_panel(axA, STAGES_FULL,  "Total per-query wall time",      annotate_pct=True)
    draw_panel(axB, STAGES_TOOLS, "Tool execution only (zoom)",     annotate_pct=False)

    # Single shared legend on the right of the figure.
    handles_A, labels_A = axA.get_legend_handles_labels()
    handles_B, labels_B = axB.get_legend_handles_labels()
    seen: set[str] = set()
    handles, labels = [], []
    for h, l in zip(handles_A + handles_B, labels_A + labels_B, strict=True):
        if l not in seen:
            seen.add(l)
            handles.append(h)
            labels.append(l)
    fig.legend(handles, labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
               frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT / "fig2_chemcrow_molecule_flip.pdf")
    plt.close(fig)
    print(f"[plot2] medium n={len(groups['medium'])} heavy n={len(groups['heavy'])}")


# =============================================================================
# PLOT 1B — fig1b_four_panel_vs_raj.pdf
# =============================================================================

def plot_1b_four_panel_vs_raj() -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))

    # Hardcoded Raj proportions per workload (from Section 3.2.1, Figure 2).
    # These are schematic recreations for visual comparison — not exact pixel
    # extractions from the published figures.
    RAJ = {
        "FreshQA": [
            ("URL fetch",          15, COLOR_TOOL_NETWORK),
            ("LexRank summarize",  55, COLOR_TOOL_COMPUTE),
            ("LLM inference",      30, COLOR_LLM),
        ],
        "ChemCrow (heavy)": [
            ("RDKit conformer",    85, COLOR_TOOL_COMPUTE),
            ("LLM inference",      12, COLOR_LLM),
            ("Other tools",         3, COLOR_OTHER),
        ],
        "SWE-Agent": [
            ("Bash / Python",      38, COLOR_TOOL_COMPUTE),
            ("LLM inference",      60, COLOR_LLM),
            ("Other",               2, COLOR_OTHER),
        ],
        "Toolformer": [
            ("Calculator (Wolfram API)", 12, COLOR_TOOL_COMPUTE),
            ("LLM inference",            88, COLOR_LLM),
        ],
    }

    def ours_pct(traces: list[dict]) -> list[tuple[str, float, str]]:
        """Return [(stage_name, %, color)] from pooled wall sums (always sums to 100)."""
        sum_llm = sum_tools = sum_orch = sum_other = 0.0
        for t in traces:
            ch = _direct_child_walls(t)
            root = _root(t)
            llm = sum(v for k, v in ch.items() if k.startswith("llm."))
            tools = sum(v for k, v in ch.items() if k.startswith("tool."))
            other_named = sum(v for k, v in ch.items()
                              if not k.startswith("llm.") and not k.startswith("tool."))
            orch = max(root["wall_time_ms"] - llm - tools - other_named, 0.0)
            sum_llm += llm
            sum_tools += tools
            sum_orch += orch
            sum_other += other_named
        total = sum_llm + sum_tools + sum_orch + sum_other
        if total <= 0:
            return []
        return [
            ("Tools",         sum_tools / total * 100, COLOR_TOOL_COMPUTE),
            ("LLM",           sum_llm   / total * 100, COLOR_LLM),
            ("Orchestration", sum_orch  / total * 100, COLOR_ORCH),
            ("Other",         sum_other / total * 100, COLOR_OTHER),
        ]

    # Gather "ours" traces per workload.
    fq_traces = _load_traces(REPO / "benchmark" / "results" / "cell_haiku_agentic")
    cc_groups = _chemcrow_n1_traces_by_label()
    swe_root = REPO / "benchmark" / "output" / "sweep_sweagent" / "python_n1"
    swe_traces = []
    for d in sorted(swe_root.glob("run*")):
        if d.is_dir(): swe_traces.extend(_load_traces(d))
    tf_root = REPO / "benchmark" / "output" / "sweep_toolformer" / "python_n1"
    tf_traces = []
    for d in sorted(tf_root.glob("run*")):
        if d.is_dir(): tf_traces.extend(_load_traces(d))

    OURS = {
        "FreshQA":          ours_pct(fq_traces),
        "ChemCrow (heavy)": ours_pct(cc_groups["heavy"]),
        "SWE-Agent":        ours_pct(swe_traces),
        "Toolformer":       ours_pct(tf_traces),
    }

    panels = [("FreshQA", axes[0, 0]),
              ("ChemCrow (heavy)", axes[0, 1]),
              ("SWE-Agent", axes[1, 0]),
              ("Toolformer", axes[1, 1])]

    # Track which stage names we've added to a global legend.
    legend_seen: dict[str, tuple] = {}

    for workload, ax in panels:
        raj_stages = RAJ[workload]
        ours_stages = OURS[workload]

        # Bar 0 = Raj, Bar 1 = ours; both normalized to 100.
        for x, stages in [(0, raj_stages), (1, ours_stages)]:
            bottom = 0.0
            for name, pct, color in stages:
                if pct <= 0:
                    continue
                bar_kw = {}
                if name not in legend_seen:
                    legend_seen[name] = (color,)
                    bar_kw["label"] = name
                ax.bar(x, pct, bottom=bottom, color=color, edgecolor="white",
                       width=0.6, **bar_kw)
                if pct >= 10:
                    ax.text(x, bottom + pct / 2, f"{pct:.0f}%", ha="center",
                            va="center", fontsize=9, color="white")
                bottom += pct

        ax.set_xlim(-0.6, 1.6)
        ax.set_ylim(0, 100)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Raj et al.", "This work"])
        ax.set_title(workload, fontsize=10, fontweight="normal")
        if ax in (axes[0, 0], axes[1, 0]):
            ax.set_ylabel("% of wall time")
        ax.set_yticks([0, 25, 50, 75, 100])

    # Single shared legend at the top, deduplicated by name. Build handles fresh.
    handles, labels = [], []
    seen = set()
    for ax in axes.flat:
        for h, l in zip(*ax.get_legend_handles_labels(), strict=True):
            if l not in seen:
                seen.add(l)
                handles.append(h)
                labels.append(l)
    fig.legend(handles, labels, loc="upper center", ncol=min(len(labels), 5),
               bbox_to_anchor=(0.5, 1.02), frameon=False, fontsize=9)

    fig.tight_layout()
    fig.subplots_adjust(top=0.88)
    fig.savefig(OUT / "fig1b_four_panel_vs_raj.pdf")
    plt.close(fig)
    for w, stages in OURS.items():
        print(f"[plot1b] {w} (ours): " + ", ".join(f"{n}={p:.1f}%" for n, p, _ in stages))


# =============================================================================
# PLOT 7 — fig7_taxonomy_comparison.pdf
# =============================================================================

def plot_7_taxonomy_comparison() -> None:
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.axis("off")
    ax.grid(False)

    headers = ["Workload", "Orchestrator", "Path", "Repetitiveness",
               "Bottleneck (Raj)", "Bottleneck (this work)", "Replicated?"]
    rows = [
        ["FreshQA (Web-Aug.)", "Host", "Static",  "Single-step",
         "LexRank 55%",            "Orchestration 51% †",          "Yes"],
        ["ChemCrow",            "LLM",  "Dynamic", "Multi-step",
         "RDKit 85% (heavy)",       "LLM 92% (heavy)",              "Yes"],
        ["SWE-Agent",           "LLM",  "Static",  "Multi-step",
         "LLM 60% / Bash 38%",      "LLM 91%",                       "Yes (synthetic)"],
        ["Toolformer",          "LLM",  "Dynamic", "Single-step",
         "LLM 88%",                 "LLM 96%",                       "Yes"],
        ["RAG (Haystack)",      "Host", "Static",  "Single-step",
         "ENNS 83%",                "Not measured",                  "No: 115 GB corpus; FAISS lacks Go-native equivalent"],
    ]

    table = ax.table(
        cellText=rows,
        colLabels=headers,
        cellLoc="left",
        loc="center",
        colWidths=[0.14, 0.09, 0.08, 0.10, 0.16, 0.16, 0.27],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.45)

    # Style header
    for j in range(len(headers)):
        cell = table[0, j]
        cell.set_text_props(weight="bold")
        cell.set_facecolor("#EEEEEE")
        cell.set_edgecolor("#888888")

    # Style body cells
    for i in range(1, len(rows) + 1):
        for j in range(len(headers)):
            cell = table[i, j]
            cell.set_edgecolor("#CCCCCC")
            # Shade the "not replicated" row slightly gray
            if i == 5:  # RAG row
                cell.set_facecolor("#F5F5F5")
                cell.set_text_props(style="italic")

    # Footnote for FreshQA orchestration
    fig.text(0.02, 0.02,
             "† FreshQA orchestration share is dominated by the 15 s rate-limit pause between LLM turns, not agent overhead.",
             fontsize=7, color="#555555", style="italic")

    fig.savefig(OUT / "fig7_taxonomy_comparison.pdf")
    plt.close(fig)
    print("[plot7] table written")


# =============================================================================
# PLOT 8 — fig8_chemcrow_throughput_concurrency.pdf
# =============================================================================

def plot_8_chemcrow_throughput_concurrency() -> None:
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8, 3.5))

    sweep_root = REPO / "benchmark" / "output" / "sweep"
    MODES = [
        ("py-mt", "Py threads",     "#C62828", "o", "-"),
        ("py-mp", "Py processes",   "#1565C0", "s", "-"),
        ("go",    "Go goroutines",  "#2E7D32", "^", "-"),
    ]
    NS_FULL = [1, 2, 4, 8, 16]

    def load(mode: str, n: int) -> dict | None:
        p = sweep_root / f"{mode}_n{n}" / "summary.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())

    for mode, label, color, marker, ls in MODES:
        ns_avail, qps_vals, wall_vals = [], [], []
        for n in NS_FULL:
            sm = load(mode, n)
            if sm is None:
                continue
            qps = sum(r["throughput_qps"] for r in sm["runs"]) / max(len(sm["runs"]), 1)
            wall = sm["gross_wallclock_ms"]["p50"]
            ns_avail.append(n)
            qps_vals.append(qps)
            wall_vals.append(wall)
        axA.plot(ns_avail, qps_vals,  color=color, marker=marker, linestyle=ls, label=label)
        axB.plot(ns_avail, wall_vals, color=color, marker=marker, linestyle=ls, label=label)

    axA.set_xscale("log", base=2)
    axA.set_xticks(NS_FULL)
    axA.set_xticklabels([str(n) for n in NS_FULL])
    axA.set_xlabel("Concurrency N")
    axA.set_ylabel("Throughput (queries / sec)")
    axA.legend(loc="upper left", frameon=False, fontsize=9)

    axB.set_xscale("log", base=2)
    axB.set_yscale("log")
    axB.set_xticks(NS_FULL)
    axB.set_xticklabels([str(n) for n in NS_FULL])
    axB.set_xlabel("Concurrency N")
    axB.set_ylabel("Wall p50 per query (ms, log)")

    fig.tight_layout()
    fig.savefig(OUT / "fig8_chemcrow_throughput_concurrency.pdf")
    plt.close(fig)
    print("[plot8] saved (note: py-mp_n16 absent — pruned in original sweep for memory)")


# =============================================================================
# PLOT 3 / 4 — cross-language wall + cpu vs N
# =============================================================================

def _summary(sweep_root: Path, lang: str, n: int) -> dict:
    p = sweep_root / f"{lang}_n{n}" / "summary.json"
    return json.loads(p.read_text())


def _plot_cross_lang(sweep_root: Path, ns: list[int], outname: str) -> None:
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8, 3.5))

    py_wall = [_summary(sweep_root, "python", n)["gross_wallclock_ms"]["p50"] for n in ns]
    go_wall = [_summary(sweep_root, "go",     n)["gross_wallclock_ms"]["p50"] for n in ns]
    py_cpu  = [_summary(sweep_root, "python", n)["agent_cpu_time_ms"]["p50"] for n in ns]
    go_cpu  = [_summary(sweep_root, "go",     n)["agent_cpu_time_ms"]["p50"] for n in ns]

    axA.plot(ns, py_wall, color="#C62828", marker="o", linestyle="-", label="Python")
    axA.plot(ns, go_wall, color="#2E7D32", marker="^", linestyle="--", label="Go")
    axA.set_xscale("log", base=2)
    axA.set_xticks(ns)
    axA.set_xticklabels([str(n) for n in ns])
    axA.set_xlabel("Concurrency N")
    axA.set_ylabel("Wall time p50 (ms)")
    # y range starting from 15s for sweagent (≈19s baseline) or from a sensible floor for toolformer
    ymin = min(min(py_wall), min(go_wall)) * 0.85
    axA.set_ylim(max(0, ymin), max(max(py_wall), max(go_wall)) * 1.1)

    axB.plot(ns, py_cpu, color="#C62828", marker="o", linestyle="-", label="Python")
    axB.plot(ns, go_cpu, color="#2E7D32", marker="^", linestyle="--", label="Go")
    axB.set_xscale("log", base=2)
    axB.set_xticks(ns)
    axB.set_xticklabels([str(n) for n in ns])
    axB.set_xlabel("Concurrency N")
    axB.set_ylabel("agent_cpu_time_ms p50")
    axB.set_ylim(0, max(max(py_cpu), max(go_cpu)) * 1.1)

    handles, labels = axA.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout()
    fig.subplots_adjust(top=0.88)
    fig.savefig(OUT / outname)
    plt.close(fig)
    print(f"[{outname}] py_wall={py_wall} go_wall={go_wall}")
    print(f"[{outname}] py_cpu={py_cpu}  go_cpu={go_cpu}")


def plot_3_sweagent_cross_language() -> None:
    sweep_root = REPO / "benchmark" / "output" / "sweep_sweagent"
    _plot_cross_lang(sweep_root, [1, 2, 4, 8], "fig3_sweagent_cross_language.pdf")


def plot_4_toolformer_cross_language() -> None:
    sweep_root = REPO / "benchmark" / "output" / "sweep_toolformer"
    _plot_cross_lang(sweep_root, [1, 2, 4, 8, 16], "fig4_toolformer_cross_language.pdf")


# =============================================================================
# PLOT 5 — fig5_phase1_vs_phase2_reconciliation.pdf
# =============================================================================

def plot_5_phase1_vs_phase2() -> None:
    # Hardcoded Phase 1 values (from prior diagnostic reports — not in summary.json).
    # Phase 2 values: VERIFY against summary.json before plotting.
    swe_root = REPO / "benchmark" / "output" / "sweep_sweagent"
    tf_root  = REPO / "benchmark" / "output" / "sweep_toolformer"

    def ratio(root: Path, lang: str) -> float:
        s1 = _summary(root, lang, 1)["gross_wallclock_ms"]["p50"]
        s8 = _summary(root, lang, 8)["gross_wallclock_ms"]["p50"]
        return s8 / s1

    phase2 = {
        ("SWE-Agent", "Python"):   ratio(swe_root, "python"),
        ("SWE-Agent", "Go"):       ratio(swe_root, "go"),
        ("Toolformer", "Python"):  ratio(tf_root, "python"),
        ("Toolformer", "Go"):      ratio(tf_root, "go"),
    }
    phase1 = {
        ("SWE-Agent", "Python"):   2.18,
        ("SWE-Agent", "Go"):       1.46,
        ("Toolformer", "Python"):  3.11,
        ("Toolformer", "Go"):      5.04,
    }

    print("[plot5] verification — Phase 2 ratios computed:")
    for k, v in phase2.items():
        print(f"   {k}: {v:.3f}x")

    groups = [("SWE-Agent", "Python"), ("SWE-Agent", "Go"),
              ("Toolformer", "Python"), ("Toolformer", "Go")]
    labels = [f"{w}\n{l}" for (w, l) in groups]
    p1_vals = [phase1[k] for k in groups]
    p2_vals = [phase2[k] for k in groups]

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(groups))
    bw = 0.36
    bars1 = ax.bar(x - bw / 2, p1_vals, bw, color="#BDBDBD",
                   label="Phase 1 diagnostic (3 queries × 1 sample)")
    bars2 = ax.bar(x + bw / 2, p2_vals, bw, color="#424242",
                   label="Phase 2 full sweep (40–60 samples)")

    for b, v in zip(bars1, p1_vals, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f}x",
                ha="center", va="bottom", fontsize=8)
    for b, v in zip(bars2, p2_vals, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f}x",
                ha="center", va="bottom", fontsize=8)

    ax.axhline(1.0, color="black", linestyle=":", linewidth=1)
    ax.text(len(groups) - 0.5, 1.05, "no scaling penalty", fontsize=8,
            ha="right", va="bottom", color="black")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Wall time scaling ratio (N=8 / N=1)")
    ax.set_ylim(0, max(p1_vals + p2_vals) * 1.15)
    ax.legend(loc="upper right", frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT / "fig5_phase1_vs_phase2_reconciliation.pdf")
    plt.close(fig)


# =============================================================================
# PLOT 6 — fig6_bottleneck_summary.pdf
# =============================================================================

def plot_6_bottleneck_summary() -> None:
    fig, ax = plt.subplots(figsize=(6, 4))

    # 5 rows (workloads) × 4 columns (stages, % of wall)
    workloads = ["FreshQA", "ChemCrow (heavy)", "ChemCrow (medium)",
                 "SWE-Agent", "Toolformer"]
    columns = ["LLM", "Tools", "Orchestration", "Other"]

    # Compute %s for each workload
    matrix = np.zeros((len(workloads), len(columns)))

    def stage_pct(traces: list[dict]) -> list[float]:
        """[LLM%, Tools%, Orchestration%, Other%] from pooled wall sums.

        Pooled (rather than median-of-percent) so the four columns sum to
        exactly 100% per row — the heatmap reads as a real allocation.
        """
        sum_llm = sum_tools = sum_orch = sum_other = 0.0
        for t in traces:
            ch = _direct_child_walls(t)
            root = _root(t)
            total = root["wall_time_ms"]
            llm = sum(v for k, v in ch.items() if k.startswith("llm."))
            tools = sum(v for k, v in ch.items() if k.startswith("tool."))
            other_named = sum(v for k, v in ch.items()
                              if not k.startswith("llm.") and not k.startswith("tool."))
            orch = max(total - llm - tools - other_named, 0.0)
            sum_llm += llm
            sum_tools += tools
            sum_orch += orch
            sum_other += other_named
        total_all = sum_llm + sum_tools + sum_orch + sum_other
        if total_all <= 0:
            return [0.0, 0.0, 0.0, 0.0]
        return [v / total_all * 100 for v in (sum_llm, sum_tools, sum_orch, sum_other)]

    # Row 0: FreshQA
    fq = _load_traces(REPO / "benchmark" / "results" / "cell_haiku_agentic")
    matrix[0] = stage_pct(fq)

    # Rows 1, 2: ChemCrow heavy / medium  (py-mp_n1, all 3 runs combined)
    cc_root = REPO / "benchmark" / "output" / "sweep" / "py-mp_n1" / "traces"
    cc_traces: list[dict] = []
    for run_dir in sorted(cc_root.glob("run*")):
        if run_dir.is_dir():
            cc_traces.extend(_load_traces(run_dir))
    cc_heavy  = [t for t in cc_traces if t.get("label") == "heavy"]
    cc_medium = [t for t in cc_traces if t.get("label") == "medium"]
    matrix[1] = stage_pct(cc_heavy)
    matrix[2] = stage_pct(cc_medium)

    # Row 3: SWE-Agent (python_n1, all 3 runs combined)
    swe_root = REPO / "benchmark" / "output" / "sweep_sweagent" / "python_n1"
    swe_traces: list[dict] = []
    for run_dir in sorted(swe_root.glob("run*")):
        if run_dir.is_dir():
            swe_traces.extend(_load_traces(run_dir))
    matrix[3] = stage_pct(swe_traces)

    # Row 4: Toolformer (python_n1, all 3 runs combined)
    tf_root = REPO / "benchmark" / "output" / "sweep_toolformer" / "python_n1"
    tf_traces: list[dict] = []
    for run_dir in sorted(tf_root.glob("run*")):
        if run_dir.is_dir():
            tf_traces.extend(_load_traces(run_dir))
    matrix[4] = stage_pct(tf_traces)

    print("[plot6] matrix (% of wall):")
    for i, w in enumerate(workloads):
        print(f"   {w:<22s} {matrix[i].round(1).tolist()}")

    im = ax.imshow(matrix, cmap="Blues", aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(len(columns)))
    ax.set_xticklabels(columns)
    ax.set_yticks(range(len(workloads)))
    ax.set_yticklabels(workloads)

    # Annotate
    for i in range(len(workloads)):
        for j in range(len(columns)):
            v = matrix[i, j]
            color = "white" if v > 50 else "black"
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center",
                    fontsize=11, color=color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.04)
    cbar.set_label("% of wall time", fontsize=9)
    ax.grid(False)

    fig.tight_layout()
    fig.savefig(OUT / "fig6_bottleneck_summary.pdf")
    plt.close(fig)


# =============================================================================

def main() -> None:
    _setup_style()
    plot_1_freshqa_vs_raj()
    plot_1b_four_panel_vs_raj()
    plot_2_chemcrow_molecule_flip()
    plot_3_sweagent_cross_language()
    plot_4_toolformer_cross_language()
    plot_5_phase1_vs_phase2()
    plot_6_bottleneck_summary()
    plot_7_taxonomy_comparison()
    plot_8_chemcrow_throughput_concurrency()
    print("\nAll plots written to:", OUT)


if __name__ == "__main__":
    main()
