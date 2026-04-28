# Figures 4–7: tests, results, and conclusions

A walkthrough of the four secondary figures in the paper. For each figure: what was tested, how, what came out, and what it lets us conclude. All four use the same trace-based instrumentation (per-query JSON spans recorded by the framework).

Source plot scripts:
- `benchmark/plots_paper/make_fig4.py`
- `benchmark/plots_paper/make_fig5_gil_concurrency.py`
- `benchmark/plots_paper/make_fig6_dataset_replication.py`
- `benchmark/plots_paper/make_fig7_w1b_hotpot_extension.py`

---

## Figure 4 — Architecture × Model comparison

### What was tested
A 4-cell factorial: `{Haiku 4.5, Opus 4.7} × {agentic loop, hardcoded pipeline}`, plus a 5th bar reproducing Raj et al. 2024 (gpt-oss-20b) for external anchoring. The question being answered: **when you change the orchestrator architecture vs. when you change the model, which one moves the cost breakdown more?**

### How
- 4 cells run on FreshQA-20 (20 queries each, n=20).
- Agentic = LLM-driven tool-use loop (3–5 LLM calls per query).
- Pipeline = hardcoded `web_search → fetch_url ×2 → lexrank_summarize ×2 → 1 LLM call`.
- Each bar is a stacked share of *active* wall time per query (wall time minus `llm.retry_wait` spans), averaged across 20 queries with 95% CI error caps on each stage segment.
- Stages: LLM, tool.search, tool.fetch, tool.summarize, retry_wait, other.

### Results
- **Agentic block (bars 1–2):** Haiku → Opus swap shifts LLM share by **+9.3 pp** (more calls per query × per-call cost).
- **Pipeline block (bars 3–4):** the same swap shifts LLM share by only **+0.1 pp** — the pipeline structurally caps LLM at exactly one call per query.
- **Architecture swap (bar 2 vs. bar 3, or bar 1 vs. bar 4):** at fixed model, swapping agentic → pipeline shifts LLM share by **~20–30 pp**.
- `tool.summarize` is visible only on the pipeline bars: the agentic loop never invoked summarization across the 40 agentic runs; the pipeline always summarizes each fetched page.

### Conclusion
**The orchestrator architecture is the single largest cost-breakdown lever measured** — far larger than the model swap. A Haiku → Opus swap is a ~+9 pp move at most (in agentic) and essentially zero in pipeline; an agentic → pipeline swap is a ~20–30 pp move at constant model. Engineering for cost-shape should prioritize architecture choices over model choices.

---

## Figure 5 — GIL / concurrency sweep (Workload 1b, Experiment A)

### What was tested
Whether Python's GIL imposes a measurable per-stage tax on a CPU-bound stage (LexRank summarization) under concurrent batches, vs. Go's goroutines (no GIL). Hypothesis (echoing Raj et al. 2024 Fig. 4c): Python `tool.summarize` should grow super-linearly with batch size because LexRank is pure-Python NumPy.

### How
- Same FreshQA-20 pipeline run in two language implementations.
  - Python: `ThreadPoolExecutor` with the `ddgs` package and `sumy` LexRank + NLTK Punkt.
  - Go: goroutines with stdlib `net/http` + regex DDG scraper + hand-coded LexRank.
- Batch sizes swept: 1, 4, 16, 64.
- Each `(lang, batch)` cell run with `SEARCH_BACKEND=ddg` (live DDG).
- Validation cells at b=64 with `SEARCH_BACKEND=static` (canned URLs) — removes DDG's anti-scraping confound and isolates pipeline+LLM cost.
- Two panels:
  - (A) throughput (q/s) vs. batch size.
  - (B) P50 `tool.summarize` wall time (ms) vs. batch size.

### Results
- **Panel A (throughput, DDG):** Python climbs monotonically across the four batch sizes with diminishing returns per 4× step. Go's curve is contaminated by an empty-search collapse — DDG returned 0 URLs in 13/20 (b=4), 20/20 (b=16), 20/20 (b=64) Go queries because the Go scraper is naive. Hollow markers in the plot flag those degraded points.
- **Panel A (static b=64 diamonds):** with DDG removed, **both languages tie at exactly 1.11 q/s** — the pipeline+LLM workload is identical when the network bottleneck is gone.
- **Panel B (P50 summarize):** Python summarize grows **only ~1.6×** from b=1 to b=64. The signal exists but is much weaker than Raj's +2.2× from b=64 to b=128. LexRank on FreshQA's short news-clip pages is too cheap (~100 ms CPU) for GIL contention to dominate.
- **Rate-limit side observation:** Go's b=64 DDG cell hit 13 Anthropic 429s because the empty-fetch short-circuit caused all 20 LLM calls to fire in a tight window — broken upstream stages cascade into rate-limit pressure on the LLM.

### Conclusion
- The GIL has a **measurable but weak** effect on this workload — the FreshQA pages are too small for LexRank contention to swamp other costs.
- The static b=64 tie is the cleanest finding: **once you remove the network noise, Python and Go achieve identical throughput** on this pipeline. The win in Go is therefore not from goroutines per se but would have to come from a different bottleneck (e.g. a heavier CPU stage).
- The Go scraper's failure under concurrency is itself a finding: **library choice (the `ddgs` package vs. naive regex) had a bigger effect than language runtime choice** on this benchmark.
- Failure cascades up: a broken fetch stage triggers Anthropic rate limits because the rest of the pipeline accelerates.

---

## Figure 6 — Dataset replication: FreshQA vs. HotpotQA (Workload 1c, Experiment B)

### What was tested
Whether the cost-breakdown shape from Fig. 4 generalizes across datasets, by re-running **Haiku 4.5 + pipeline** on a second benchmark (HotpotQA-20: 7 bridge + 7 comparison-entity + 6 comparison-yesno, selection_seed=42) while holding architecture, model, and instrumentation constant.

### How
- Two cells, only the query set differs:
  - `cell_haiku_pipeline` — FreshQA-20.
  - `cell_haiku_pipeline_hotpot` — HotpotQA-20.
- Two panels:
  - (A) stacked percentage breakdown (mean per-query share).
  - (B) absolute **median** per-query wall time per stage, side-by-side bars with FreshQA → HotpotQA delta annotations on the HotpotQA bars.
- **Median (not mean)** in Panel B is the deliberate honest-comparison choice — the W1b extension (Fig. 7) showed mean is dominated by tail outliers (one ~22 s pathological Wikipedia page per HotpotQA batch).

### Results
- **Panel A:** both datasets sit in the same regime — tool-dominant by a wide margin (≈84–87% tool, ≈13–16% LLM). The cost SHAPE is dataset-independent.
- **Panel B:** at the typical query the per-stage MEDIANS are close across datasets — small percentage deltas on LLM, search, fetch, and summarize.
- **Caveat:** HotpotQA dev distractor only contains hard examples; FreshQA covers a broader difficulty range. So the cells are comparable for "does the shape generalize?" but NOT a difficulty-controlled comparison.
- An earlier mean-based version of this figure showed **+637% summarize** and **7.4× per-call summarize** on HotpotQA — both turned out to be tail-outlier artifacts and were corrected when this figure was rebased on medians.

### Conclusion
- **The cost-breakdown shape generalizes across datasets**, which makes the architecture-vs-model finding from Fig. 4 robust — it isn't a peculiarity of FreshQA.
- The **mean-vs-median** correction is itself an important finding: aggregate stats over 20-query benchmarks are fragile to single pathological pages, and the honest comparison metric is the median.
- Token-budget across datasets is essentially flat (input −14% on HotpotQA, output ±1.4%) — the LLM stage behaves the same on both datasets. Variance lives on the tool side, but even there the typical query is comparable.

---

## Figure 7 — W1b extension: HotpotQA vs. FreshQA P50 summarize sweep

### What was tested
A direct test of Fig. 5's "GIL signal was attenuated because the workload was too cheap" explanation. If that explanation holds, a heavier-per-call workload (HotpotQA's Wikipedia pages — initially advertised as 7.4× heavier per summarize call) should produce a steeper P50 summarize curve as batch size grows.

### How
- Repeats the Python concurrent sweep from Fig. 5 at batch sizes 1, 4, 16, 64 — but on **HotpotQA-20** instead of FreshQA-20.
- Overlays both curves on the same axes.
- Two panels:
  - (A) P50 `tool.summarize` per query — typical-query view.
  - (B) Mean `tool.summarize` per query — outlier-sensitive view, included to make the artifact visible.

### Results
- **Panel A (P50, the honest view):** the two curves grow at **essentially the same rate** — ≈1.59× on FreshQA vs. ≈1.55× on HotpotQA from b=1 to b=64. The "heavier workload should bend the curve" hypothesis is **not supported on this data**.
- **Panel B (mean):** HotpotQA's mean is much more volatile because ~1/20 queries hits a 5–22 s pathological Wikipedia page. The HotpotQA mean even *drops* at b=64 because DDG returned different (smaller) URLs under heavy concurrent load — a workload-side effect, not a runtime-side one. Mean is unreliable here.
- **Diagnostic conclusion:** the W1c "+637% / 7.4× per-call" claim was a mean-vs-median artifact (see Fig. 6). At the median, HotpotQA per-call summarize cost is only ≈1.3× FreshQA's. So this extension did not actually run a meaningfully heavier workload at the typical query.

### Conclusion
- The GIL hypothesis is **not refuted** — it's **untested at scale**. To test it properly would need a workload whose **median** (not just its tail) is materially heavier than FreshQA's.
- The W1b "lightweight LexRank" explanation is now uncertain rather than supported.
- This figure is also a methodology lesson: anchoring claims on means in small (n=20) benchmarks invites tail-outlier artifacts. The instrumentation is calibrated correctly; the weakness was in the workload selection, not in the measurement framework.

---

## Cross-figure summary

| Figure | Lever varied | Holds constant | Headline result |
|---|---|---|---|
| 4 | Architecture × Model | Dataset (FreshQA) | Architecture shifts LLM share ~20–30 pp; model shifts at most ~9 pp (agentic) or ~0 pp (pipeline). Architecture is the dominant lever. |
| 5 | Language runtime × batch size | Architecture, model, dataset | Static b=64 ties at 1.11 q/s; Python summarize grows only ~1.6× from b=1→b=64. GIL signal weak on this workload; library choice (DDG scraper) matters more than runtime. |
| 6 | Dataset (FreshQA vs. HotpotQA) | Architecture, model | Cost-breakdown shape generalizes; median per-stage costs comparable. Mean-based +637% summarize claim was a tail-outlier artifact. |
| 7 | Dataset on the W1b sweep | Language, architecture, model | P50 summarize grows ~1.55× (HotpotQA) vs. ~1.59× (FreshQA) — essentially identical. The "too-cheap workload" explanation is not supported by this data; GIL hypothesis remains untested at scale. |

Together, the four figures triangulate: **architecture is the dominant cost-shape lever (Fig. 4); that finding generalizes across datasets (Fig. 6); language runtime contributes much less than expected on this workload (Fig. 5); and the standard mitigating explanation for the weak runtime effect doesn't hold up under direct test (Fig. 7)**. The honest summary is that orchestrator architecture is the lever practitioners should pull, and that small-n benchmark claims should be reported on medians, not means.
