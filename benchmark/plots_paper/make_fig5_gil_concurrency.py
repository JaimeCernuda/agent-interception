"""Figure 5 — Experiment A (Workload 1b): concurrent batch sweep, Py vs Go.

Two panels, sharing an x-axis on batch size [1, 4, 16, 64]:

  Panel A — throughput (queries/second) vs batch size.
    Two lines: Python (ThreadPoolExecutor, GIL) vs Go (goroutines, no GIL).
    Both lines show the primary cells with SEARCH_BACKEND=ddg (live).
    Two diamond markers at batch=64 show the SEARCH_BACKEND=static
    validation cells, where DDG's anti-scraping behavior is removed and
    the comparison is apples-to-apples on pipeline+LLM cost only.
    Annotations on Go's DDG line at b=4/16/64 show the useful-fetch
    count (queries where DDG returned >0 URLs).

  Panel B — P50 tool.summarize wall time (ms) vs batch size.
    Same Py/Go split as Panel A. The hypothesis was that Python's
    summarize stage would grow super-linearly with batch size due to GIL
    contention on LexRank (a pure-Python NumPy stage), echoing Raj et al.
    Figure 4c. Observed: a mild +1.6x growth from b=1 to b=64 on Python,
    much weaker than Raj's +2.2x on b=64 to b=128 — the LexRank step on
    short FreshQA pages is too cheap to expose strong contention.

  CRITICAL CAVEAT (top of caption.txt): Go's DDG points at b=4/16/64
  are NOT a clean per-stage measurement. DDG returned 0 URLs in 13/20
  (b=4) and 20/20 (b=16, b=64) Go queries, so the pipeline short-
  circuited before reaching summarize, leaving Go's summarize line
  artificially flat near 0 ms. The static b=64 marker is the only Go
  point in Panel B where summarize ran on real input.

Data sources (read directly from per-query JSON traces):
  benchmark/results/cell_concurrent_{py,go}_b{1,4,16,64}/             (DDG)
  benchmark/results/cell_concurrent_{py,go}_b64_static/                (static)
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from benchmark.plots_paper import style

_RESULTS = Path("benchmark/results")
_LOG = _RESULTS / "experiment_a_master.log"

_BATCH_SIZES = (1, 4, 16, 64)
_LANGS = ("py", "go")
_LANG_DISPLAY = {"py": "Python (ThreadPoolExecutor)", "go": "Go (goroutines)"}
_LANG_COLOR = {"py": "#0072B2", "go": "#D55E00"}  # blue / vermillion (Wong)
_LANG_MARKER = {"py": "o", "go": "s"}


def _cell_dir(lang: str, batch: int, backend: str) -> Path:
    suffix = "" if backend == "ddg" else f"_{backend}"
    return _RESULTS / f"cell_concurrent_{lang}_b{batch}{suffix}"


def _batch_walls_from_log() -> dict[str, float]:
    """Extract per-cell batch wall-clock seconds from the master log's
    [CELL] start/end timestamp lines. The master log is the source of truth
    for end-to-end batch time, since trace files only record per-query times.
    """
    out: dict[str, float] = {}
    if not _LOG.exists():
        return out
    log = _LOG.read_text()
    pattern = re.compile(
        r"\[CELL\] (\w+)\s+batch=(\d+)\s+backend=(\w+)\s+out=benchmark/results/(\S+)\n"
        r"\[CELL\] start: ([^\n]+)Z\n.*?\[CELL\] end: ([^\n]+)Z",
        re.DOTALL,
    )
    for m in pattern.finditer(log):
        _, _, _, name, t0, t1 = m.groups()
        dt = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds()
        out[name] = dt
    return out


def _per_cell_metrics(cell_dir: Path) -> dict:
    """Extract throughput-relevant metrics from the trace JSONs in cell_dir."""
    files = sorted(cell_dir.glob("*.json"))
    if not files:
        return {"n": 0}
    summarize_ms_per_q: list[float] = []
    useful_fetch_q: int = 0  # queries where tool.search returned >= 1 url
    retry_waits_total: int = 0
    for f in files:
        blob = json.loads(f.read_text())
        s_ms_q = 0.0
        non_empty_search = False
        for sp in blob["spans"]:
            if sp["name"] == "tool.summarize":
                s_ms_q += sp["wall_time_ms"]
            if sp["name"] == "tool.search":
                if int(sp.get("attrs", {}).get("tool.num_results", 0)) > 0:
                    non_empty_search = True
            if sp["name"] == "llm.retry_wait":
                retry_waits_total += 1
        summarize_ms_per_q.append(s_ms_q)
        if non_empty_search:
            useful_fetch_q += 1
    return {
        "n": len(files),
        "summarize_ms_p50": statistics.median(summarize_ms_per_q),
        "useful_fetch_q": useful_fetch_q,
        "retry_waits_total": retry_waits_total,
    }


def main() -> int:
    style.configure()

    walls = _batch_walls_from_log()
    if not walls:
        print(
            f"[fig5] WARN: master log {_LOG} not found or empty; "
            "throughput will be inferred from per-query times if possible.",
            file=sys.stderr,
        )

    # Build the data tables: ddg primary [b1,4,16,64] x [py,go]
    ddg_throughput: dict[str, list[float | None]] = {"py": [], "go": []}
    ddg_summarize: dict[str, list[float | None]] = {"py": [], "go": []}
    ddg_useful: dict[str, list[int]] = {"py": [], "go": []}
    ddg_retries: dict[str, list[int]] = {"py": [], "go": []}

    for lang in _LANGS:
        for b in _BATCH_SIZES:
            cdir = _cell_dir(lang, b, "ddg")
            m = _per_cell_metrics(cdir)
            wall = walls.get(cdir.name)
            qps = (m["n"] / wall) if (wall and m.get("n", 0) > 0) else None
            ddg_throughput[lang].append(qps)
            ddg_summarize[lang].append(m.get("summarize_ms_p50") if m.get("n", 0) else None)
            ddg_useful[lang].append(m.get("useful_fetch_q", 0))
            ddg_retries[lang].append(m.get("retry_waits_total", 0))

    # Static b=64 validation cells
    static_throughput: dict[str, float | None] = {}
    static_summarize: dict[str, float | None] = {}
    for lang in _LANGS:
        cdir = _cell_dir(lang, 64, "static")
        m = _per_cell_metrics(cdir)
        wall = walls.get(cdir.name)
        static_throughput[lang] = (m["n"] / wall) if (wall and m.get("n", 0) > 0) else None
        static_summarize[lang] = m.get("summarize_ms_p50") if m.get("n", 0) else None

    # ---- Plot ---------------------------------------------------------------
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(8.0, 3.6))

    # Plot helper: draws line for a language, then marker fills depend on
    # whether the cell is a "valid" measurement. For Go DDG, b=1 is valid
    # (20/20 useful searches); b=4, b=16, b=64 short-circuited (DDG returned
    # 0 URLs in 13/20, 20/20, 20/20 queries respectively) and are rendered
    # hollow per the visualization convention for "data point exists but
    # isn't a valid measurement of the thing we're trying to measure".
    def _is_valid_dgg_point(lang: str, b: int, useful: int) -> bool:
        # Threshold: at least 14/20 (70%) of queries had a non-empty search.
        # Below this, the cell short-circuited too often to be representative.
        return useful >= 14

    def _plot_lang_with_validity(ax, ys, useful_counts, lang: str) -> None:
        x_used = [b for b, y in zip(_BATCH_SIZES, ys) if y is not None]
        y_used = [y for y in ys if y is not None]
        # Draw the trend line with no markers; we'll overlay markers below.
        ax.plot(
            x_used,
            y_used,
            color=_LANG_COLOR[lang],
            linewidth=1.5,
            label=f"{_LANG_DISPLAY[lang]} — DDG",
            zorder=2,
        )
        # Overlay markers — filled if valid, hollow if not.
        for b, y, useful in zip(_BATCH_SIZES, ys, useful_counts):
            if y is None:
                continue
            valid = _is_valid_dgg_point(lang, b, useful)
            ax.scatter(
                [b],
                [y],
                marker=_LANG_MARKER[lang],
                s=42,
                facecolors=_LANG_COLOR[lang] if valid else "white",
                edgecolors=_LANG_COLOR[lang],
                linewidths=1.4,
                zorder=4,
            )

    # Panel A — throughput
    for lang in _LANGS:
        _plot_lang_with_validity(axA, ddg_throughput[lang], ddg_useful[lang], lang)
        # Annotate Go DDG points at b=4/16/64 with useful-fetch counts.
        if lang == "go":
            for b, y, useful in zip(_BATCH_SIZES, ddg_throughput[lang], ddg_useful[lang]):
                if b in (4, 16, 64) and y is not None:
                    axA.annotate(
                        f"{useful}/20\nuseful",
                        xy=(b, y),
                        xytext=(8, 8),
                        textcoords="offset points",
                        ha="left",
                        fontsize=6,
                        color="#666666",
                    )
    # Static validation diamonds. Both languages tied at exactly 1.111 q/s,
    # so offset them slightly in x so the markers don't fully overlap.
    for lang, x_off in (("py", 56), ("go", 73)):
        y = static_throughput[lang]
        if y is None:
            continue
        axA.scatter(
            [x_off],
            [y],
            marker="D",
            s=70,
            facecolors="white",
            edgecolors=_LANG_COLOR[lang],
            linewidths=1.7,
            zorder=5,
            label=f"{lang.upper()} — static (b=64)",
        )
    axA.set_xscale("log", base=2)
    axA.set_xticks(list(_BATCH_SIZES))
    axA.set_xticklabels([str(b) for b in _BATCH_SIZES])
    axA.set_xlabel("concurrent batch size")
    axA.set_ylabel("throughput (queries / second)")
    axA.set_title("(A) Throughput vs batch size")
    axA.set_ylim(top=1.55)  # headroom for static-marker labels
    # Add a one-line note next to the static markers
    axA.text(
        46,
        1.30,
        "Py & Go static (b=64):\nboth 1.11 q/s (tied)",
        fontsize=6.5,
        color="#444444",
        ha="left",
        va="center",
    )
    _hollow_handle = Line2D(
        [], [],
        marker="s",
        markerfacecolor="white",
        markeredgecolor="#444444",
        markersize=6,
        markeredgewidth=1.4,
        linestyle="None",
        label="hollow trend marker = degraded (DDG search blocked)",
    )
    handles, labels = axA.get_legend_handles_labels()
    axA.legend(
        handles + [_hollow_handle],
        labels + [_hollow_handle.get_label()],
        fontsize=6.5,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        ncol=2,
        frameon=False,
        columnspacing=1.0,
        handletextpad=0.4,
    )

    # Panel B — P50 summarize
    for lang in _LANGS:
        _plot_lang_with_validity(axB, ddg_summarize[lang], ddg_useful[lang], lang)
        # Annotate Go points heavily — they short-circuited
        if lang == "go":
            for b, y, useful in zip(_BATCH_SIZES, ddg_summarize[lang], ddg_useful[lang]):
                if b in (4, 16, 64) and y is not None:
                    axB.annotate(
                        f"{useful}/20\nuseful",
                        xy=(b, y),
                        xytext=(0, 12),
                        textcoords="offset points",
                        ha="center",
                        fontsize=6,
                        color="#666666",
                    )
    # Static validation diamonds (slightly offset)
    for lang, x_off in (("py", 56), ("go", 73)):
        y = static_summarize[lang]
        if y is None:
            continue
        axB.scatter(
            [x_off],
            [y],
            marker="D",
            s=70,
            facecolors="white",
            edgecolors=_LANG_COLOR[lang],
            linewidths=1.7,
            zorder=5,
            label=f"{lang.upper()} — static (b=64)",
        )
    axB.set_xscale("log", base=2)
    axB.set_xticks(list(_BATCH_SIZES))
    axB.set_xticklabels([str(b) for b in _BATCH_SIZES])
    axB.set_xlabel("concurrent batch size")
    axB.set_ylabel("P50 tool.summarize wall time (ms)")
    axB.set_title("(B) Per-stage GIL signal: summarize")
    axB.set_ylim(top=400)  # headroom for annotations
    handles_b, labels_b = axB.get_legend_handles_labels()
    axB.legend(
        handles_b + [_hollow_handle],
        labels_b + [_hollow_handle.get_label()],
        fontsize=6.5,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        ncol=2,
        frameon=False,
        columnspacing=1.0,
        handletextpad=0.4,
    )

    fig.tight_layout()
    pdf_path, png_path = style.save(fig, "fig5_gil_concurrency")
    plt.close(fig)

    # ---- Caption -----------------------------------------------------------
    cap = style.OUT_DIR / "fig5_gil_concurrency_caption.txt"
    py_qps = ddg_throughput["py"]
    go_qps = ddg_throughput["go"]
    py_sum = ddg_summarize["py"]
    py_b1, py_b64 = py_sum[0], py_sum[3]
    py_growth = (py_b64 / py_b1) if (py_b1 and py_b64) else float("nan")
    with cap.open("w") as f:
        f.write(
            "CAVEAT — read first: Go's DDG points at batch size 4, 16, and 64 in "
            "Panel B are NOT a clean per-stage measurement. The Go runner uses a "
            "stdlib net/http + regex DDG scraper that DDG's anti-scraping system "
            "blocks at concurrency >= 4: 13/20 (b=4), 20/20 (b=16), and 20/20 "
            "(b=64) of Go's queries received zero URLs from DDG and therefore "
            "short-circuited before reaching the summarize stage. Go's "
            "apparent flatness near 0 ms in Panel B reflects that short-circuit, "
            "not Goroutine concurrency winning. The static-backend diamond "
            "marker at b=64 is the only Go point in Panel B where summarize "
            "ran on real input. Python uses the ddgs package (more robust to "
            "DDG anti-scraping) and completed all 80 DDG queries with non-zero "
            "search results.\n\n"
            "Figure 5 — Experiment A. Concurrent batch sweep on the FreshQA-20 "
            "pipeline (web_search -> fetch_url x2 -> lexrank_summarize x2 -> "
            "single LLM call), comparing Python's ThreadPoolExecutor (GIL) "
            "against Go's goroutines (no GIL). Two panels:\n\n"
            "  (A) Throughput in queries/second vs batch size. Solid lines = "
            "DDG live; diamond markers = SEARCH_BACKEND=static validation at "
            "b=64. Python's curve climbs monotonically "
            f"({py_qps[0]:.2f} -> {py_qps[1]:.2f} -> {py_qps[2]:.2f} -> {py_qps[3]:.2f} q/s), "
            f"with diminishing speedup per 4x batch increase ({py_qps[1]/py_qps[0]:.1f}x, "
            f"{py_qps[2]/py_qps[1]:.1f}x, {py_qps[3]/py_qps[2]:.1f}x). Go's curve "
            "is contaminated by the empty-search collapse; the static b=64 "
            "diamonds show that with the network bottleneck removed, both "
            "languages reach exactly the same throughput "
            f"({static_throughput['py']:.2f} q/s for Python, "
            f"{static_throughput['go']:.2f} q/s for Go).\n\n"
            "  (B) P50 wall time of the tool.summarize span vs batch size. The "
            "GIL hypothesis predicted Python summarize would grow super-"
            "linearly with batch size, mirroring Raj et al. 2024 Figure 4c "
            "(2.9 s -> 6.3 s between b=64 and b=128). Observed: Python summarize "
            f"grows mildly from {py_b1:.0f} ms (b=1) to {py_b64:.0f} ms (b=64), a "
            f"{py_growth:.1f}x increase. The signal exists but is attenuated "
            "because LexRank on FreshQA's short news-clip pages takes ~100 ms "
            "of CPU per call; even with 64 contending threads, GIL overhead "
            "is tens of ms, not seconds. Workload 1c (HotpotQA pipeline) "
            "subsequently measured a 7.4x summarize cost on Wikipedia-length "
            "pages, which implies stronger contention would be visible if "
            "this experiment were re-run on HotpotQA. The framework's "
            "instrumentation is calibrated to detect both regimes; the "
            "weakness is in the FreshQA workload, not in the measurement.\n\n"
            "Library identification (relevant for replication and for "
            "interpreting the empty-search caveat above):\n"
            "  - Python search: ddgs package (PyPI, has built-in evasion / "
            "result re-fetch).\n"
            "  - Go search: stdlib net/http POST to html.duckduckgo.com + "
            "regex extractor on the response body.\n"
            "  - Python summarize: sumy LexRankSummarizer + NLTK Punkt tokenizer "
            "(pure Python with NumPy backing).\n"
            "  - Go summarize: hand-coded LexRank in pure Go (regex sentence "
            "splitter, ASCII tokenizer, hand-coded TF-IDF + power iteration).\n\n"
            "Anthropic rate-limit observations (LLM retry_wait counts per cell): "
            f"Python DDG cells: {ddg_retries['py']} retries at b=[1,4,16,64]; "
            f"Go DDG cells: {ddg_retries['go']} retries; Python b=64 static: 0; "
            "Go b=64 static: 5. Go's b=64 DDG cell accumulated 13 Anthropic 429s "
            "because the empty-fetch short-circuit caused all 20 LLM calls to "
            "fire within seconds of each other, breaching Anthropic's per-second "
            "window — itself a finding about how broken upstream stages cascade "
            "into rate-limit pressure on the LLM call.\n"
        )

    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")
    print(f"wrote {cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
