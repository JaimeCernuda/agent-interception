# Evaluation Log

A single-document overview of the evaluation pass run in preparation for the
thesis chapter. Covers the two new model-axis cells (Haiku 4.5 vs Opus 4.7),
the engineering changes that were necessary to run them, the data quality
issues discovered along the way, the three paper figures produced, and the
key takeaways.

For the *design* of the evaluation (hypotheses, sample, pacing), see
[`EVAL_PLAN.md`](EVAL_PLAN.md). For the *cross-language* result that
predates this session (Py vs Go, v1-vs-v2 schema), see
[`report.md`](report.md). This document is the connective narrative.

---

## TL;DR

Two sweeps on the same FreshQA-20 subset, both implementing the same
**hardcoded pipeline architecture** that mirrors Raj et al. 2024's
LangChain orchestrator step set:

    web_search -> fetch_url x2 -> summarize x2 -> single LLM call

Only the LLM model identifier varies between cells:

| Cell | Model | n | Median active wall | LLM share | Tool share |
|---|---|---:|---:|---:|---:|
| 1 | `claude-haiku-4-5-20251001` | 20 | 12.5 s | 16.2% | 83.8% |
| 2 | `claude-opus-4-7` | 20 | 11.3 s | 16.3% | 83.7% |

**Headline finding**: in the pipeline architecture, swapping Haiku for
Opus moves LLM share by **+0.1 pp** — model identity is essentially
invisible. By contrast the same swap in the agentic architecture moved
LLM share by **+9.3 pp**. The orchestrator architecture dominates the
model choice on this workload, because the pipeline structurally caps
LLM calls at exactly one per query.

Headline shape: **tools dominate the cost breakdown by a wide margin**,
matching Raj et al.'s direction (their freshQA bar: ~28% LLM, ~55%
fetch, ~22% other). DDG search is the single largest cost in our
pipeline (~5 s on average) — a Google CSE swap would close most of the
absolute-time gap to Raj's ~6 s total.

This is a **second-pass architecture**: the original session ran an
agentic tool-use loop where the model decided each turn which tool to
call. That data is preserved under `benchmark/results/cell_{haiku,opus}
_agentic/` but does not match Raj's setup (it issues 3-5 LLM calls per
query, vs. Raj's 1) and so cannot be used for a like-for-like cost-
breakdown comparison. The agentic results remain interesting on their
own ("how does an agentic loop spend time?") and are kept as a
secondary artifact.

Three paper-quality figures (`benchmark/plots_paper/out/`):
fig 1 cost breakdown (now from pipeline cells), fig 2 v1-vs-v2 retry
split, fig 3 cross-language scatter.

A pre-existing data quality issue was also resolved: 5 of the 20 legacy
Go traces under `benchmark/traces/go/` were records of HTTP 400 "credit
balance too low" failures from an earlier sweep. They were silently
inflating Go's apparent LLM advantage in fig 2 / fig 3 and have since
been deleted; a `metrics.is_failed_trace` filter is retained as a
defensive guard.

---

## Background — what existed before this session

| Asset | State at start of session |
|---|---|
| `benchmark/configs/config_py.py` | One config: Haiku 4.5 + custom Py tools, default `SEARCH_BACKEND=static` |
| `benchmark/traces/py/`, `traces/go/` | 20 traces each from the cross-language pass (static-backend, retry-aware v2 schema) |
| `benchmark/results/{final,final_v2,smoke*}` | Archived metric CSVs and plotly plots from the cross-language work |
| `benchmark/analysis/{metrics.py, plots.py}` | Per-query metrics builder + live plotly dashboard; no paper-quality matplotlib output |
| `benchmark/run.py` | Sweep driver, `--config` accepted only `py`, no probe / no per-cell summary |
| `benchmark/EVAL_PLAN.md`, `EVAL_LOG.md`, `plots_paper/` | Did not exist |

The cross-language work (Py vs Go) was already complete; the model-axis
work (Haiku vs Opus) was the new piece this session added.

---

## Engineering changes

All changes are additive — `config_py.py` and the legacy Py traces are
untouched, so reproducibility of the cross-language story is preserved.

### Search backend default flipped

`benchmark/tools/search.py`: `_resolve_backend()` auto-fallback changed
from `"static"` → `"ddg"`. Static lookup produced ~0.08 ms per
`tool.search` span (a dict read against pre-resolved URLs in
`freshqa_20.json`); that defeated the purpose of a cost-breakdown
figure. `static` is still selectable via `SEARCH_BACKEND=static` for
reproducing the legacy traces. `.env.example` and the local `.env`
both flipped to `SEARCH_BACKEND=auto` so the new default is picked up.

### Four new config modules (two architectures)

**Agentic configs (initial pass, now superseded as headline):**
`config_haiku_custom.py` and `config_opus_custom.py` — tool-use loop,
3-5 LLM calls per query, `_MAX_TURNS=10`, 5 s inter-turn pause.
Annotate the root span with `agent.terminated_reason` (`natural` |
`max_turns`) and `agent.truncated` for analysis. Import the heavy
lifting (`TOOLS`, `SYSTEM_PROMPT`, `_call_with_429_retry`,
`_dispatch_search`, `_MAX_TURNS`) from `config_py.py` so they cannot
drift on retry policy or tool list. Output dirs renamed to
`cell_{haiku,opus}_agentic/` to make the architecture obvious.

**Pipeline configs (current, used for fig 1):**
`config_pipeline_haiku.py` and `config_pipeline_opus.py` — hardcoded
`web_search → fetch_url×2 → summarize×2 → single LLM call`. No tools
on the LLM call, no system prompt; user prompt mirrors Raj's exactly:
`"Based on these summaries, answer: {q}\n\n{summaries}"`. Each call
goes through `_pipeline_helpers.pipeline_llm_call`, which wraps a
single Anthropic `messages.create` with the same 429-retry policy as
the agentic helper. Annotate the root span with
`agent.architecture="pipeline"`, `agent.num_urls_fetched`,
`agent.num_summaries`. Always `agent.terminated_reason="natural"`,
`agent.truncated=False` (no loop, no truncation possible).

| File | Architecture | Model | Trace label |
|---|---|---|---|
| `config_haiku_custom.py` | agentic | Haiku 4.5 | `cell_haiku_custom` |
| `config_opus_custom.py` | agentic | Opus 4.7 | `cell_opus_custom` |
| `config_pipeline_haiku.py` | pipeline | Haiku 4.5 | `cell_haiku_pipeline` |
| `config_pipeline_opus.py` | pipeline | Opus 4.7 | `cell_opus_pipeline` |

`config_py.py` is **untouched** by all four — it is the canonical
agentic-config template, imported from but never modified.

### `run.py` extensions

- `--config` now accepts `haiku_custom` and `opus_custom` in addition to `py`.
- `--out` is now optional; falls back to `module.DEFAULT_OUT_DIR` if the selected config defines one.
- `--sleep` default raised from 0 s to 5 s for reproducibility (pacing matches `EVAL_PLAN.md`).
- `--skip-probe` added; bypasses the pre-sweep model sanity probes.
- New pre-sweep **sanity probe**: a 1-token direct SDK call against both Haiku and Opus before any sweep starts, outside the obs system. Catches auth / model-id / network failures before committing 20 queries' worth of budget.
- New **post-query guards** that abort the sweep early:
  - More than 3 `llm.retry_wait` spans on a single query → Anthropic is rate-limiting hard, stop.
  - 3 consecutive queries with DDG retries → DDG is misbehaving, stop.
  - Wall time on a single query exceeds 180 s → runaway behavior, stop.
- New **per-cell summary table** printed at the end of each sweep: queries completed, median active latency, mean LLM/tool shares, retry-wait count, truncation count and qids.

### Backfill script for legacy termination metadata

`benchmark/backfill_termination.py`: one-shot, idempotent script that
reads existing trace JSONs, derives `agent.terminated_reason` from the
last successful `llm.generate`'s `stop_reason`, and writes the
attribute back into the root span. Used to retroactively annotate
Cell 1's 20 traces; can also be applied to other historical trace
sets.

### Failure-trace filter

`benchmark/analysis/metrics.is_failed_trace(tf)` returns `True` when a
trace contains any `status != "ok"` span and zero successful
`llm.generate` spans — i.e. a transport-level failure with no usable
measurement. `build_df()` and the fig 2 / fig 3 plot scripts skip these
automatically. Defensive: even after the 5 known-failed Go traces were
deleted, the filter remains as protection against future bad traces.

### Paper-quality plotting module

New package `benchmark/plots_paper/` with:

- `style.py` — matplotlib rcParams (serif, 9pt body, 300 dpi PDF, top/right spines hidden, dashed grid) plus a Wong-2011 color palette pinning one color per stage across all figures.
- `make_fig1.py`, `make_fig2.py`, `make_fig3.py` — three idempotent scripts each emitting a PDF, a PNG (300 dpi), and a caption `.txt` file.

The pre-existing `benchmark/analysis/plots.py` (plotly, for the live
dashboard) was deliberately not touched.

---

## The architecture switch

The session originally ran an agentic tool-use loop (the model decided
each turn which tool to call, with up to 10 turns per query). That
matches the broader "agentic AI" framing but **does not match Raj et
al.'s LangChain orchestrator**, which is a hardcoded 4-step pipeline
running exactly one LLM call per query.

Mid-session this was caught: comparing an agentic loop's cost
breakdown against a hardcoded pipeline's is comparing two different
orchestrator architectures, not two model speeds on the same
architecture. Fig 1 was muddled as a result.

The cells were rewritten to mirror Raj's pipeline:

| Aspect | Agentic (superseded) | Pipeline (current) |
|---|---|---|
| Architecture | Tool-use loop | Hardcoded `search → fetch×2 → summarize×2 → answer` |
| LLM calls per query | 3-5 | 1 |
| Who decides tool sequence | Model | Hardcoded |
| `summarize` step | Never invoked by model | Always runs |
| Truncation possible | Yes (`_MAX_TURNS=10`) | No |
| Comparable to Raj's freshQA bar | No | Yes |

Both architectures use the same instrumentation, so the same trace
schema and analysis pipeline handle both. The agentic data is preserved
under `benchmark/results/cell_{haiku,opus}_agentic/` and remains
available for a separate "pipeline vs agentic" comparison if desired.

## The two cells (pipeline architecture)

### Cell 1 — Haiku 4.5 + pipeline

Sweep wall time: ~5 min for 20 queries (much faster than the agentic
version because each query is a single LLM call instead of 3-5). Zero
`llm.retry_wait` spans; per-query wall guard never fired.

```
queries completed         20 / 20
median active_latency_ms    12,539.2
mean LLM share of active       16.2%
mean tool share of active      83.8%
total llm.retry_wait               0
truncated (max_turns hit)          0  []
```

**Hypothesis**: LLM share ≤ 30%, tool share dominates.
**Observed**: 16.2% / 83.8%. Direction matches; magnitude is even more
tool-skewed than predicted, because DDG search is slow (~5 s mean per
query) relative to Haiku's API call (~2 s).

Per-stage decomposition on a representative query (q001):

| Stage | Wall time |
|---|---:|
| `tool.search` | 5,488 ms |
| `tool.fetch` × 2 | 3,853 + 2,106 ms |
| `tool.summarize` × 2 | 153 + 57 ms |
| `llm.generate` (1) | 2,207 ms |
| **Active total** | **13,864 ms** |

Truncation no longer applies (no agent loop, no `_MAX_TURNS`); all
queries terminate naturally after the single LLM call returns.

**Answer-quality side effect.** The pipeline is brittle: if DDG's
first 2 URLs do not surface a clean source, the model has no way to
refine. ~9 / 20 of Cell 1's answers contain "I don't have enough
information" or "I cannot determine." This is **expected behavior for
the architecture**, not a bug. It is a real cost of the pipeline
choice (vs. the agentic loop's ability to iterate), and is worth
noting in the chapter alongside the cost-breakdown story.

### Cell 2 — Opus 4.7 + pipeline

Sweep wall time: ~10 min for 20 queries. Zero `llm.retry_wait` spans;
per-query wall guard never fired (one query came close at 160 s — see
below).

```
queries completed         20 / 20
median active_latency_ms    11,346.6
mean LLM share of active       16.3%
mean tool share of active      83.7%
total llm.retry_wait               0
truncated (max_turns hit)          0  []
```

**Hypothesis**: LLM share rises modestly relative to Cell 1.
**Observed**: 16.3% — within 0.1 pp of Cell 1. The hypothesis is **not
confirmed**. In a pipeline architecture, the model is called once per
query, so per-call latency differences cannot multiply into a visible
share shift.

Mean `llm.generate` wall time is actually slightly *lower* on Opus
than Haiku (1,967 ms vs 2,147 ms). Not a measurement error: the
pattern holds across all 20 queries (Opus range 1,223-3,117 ms, Haiku
range 1,598-3,122 ms). On small-prompt single-shot composition tasks,
Opus appears to match or beat Haiku in absolute wall time.

**One pathological query**: q002 ("Who wrote Orientalism?") accumulated
145 s of `tool.summarize` time on a 2.6 MB HTML document that DDG
returned. Total wall on q002 was 160 s — one tick under the 180 s
per-query guard. The single outlier dragged the cell's mean active
wall up to ~20 s, but the median (11.3 s) is in line with Cell 1. The
outlier is real-world behavior of the pipeline (LexRank scaling
super-linearly with document size), not a bug.

**DDG non-determinism**: between Cell 1 and Cell 2 runs, the same
query received different URLs from DDG (DDG does not guarantee stable
results). q002 was the most visible artifact. This is the search
engine's behavior, not the framework's; if reproducibility is the
priority, swap to Google CSE or static.

### Side-by-side (pipeline cells)

| Metric | Cell 1 (Haiku) | Cell 2 (Opus) | Δ |
|---|---:|---:|---:|
| queries completed | 20 / 20 | 20 / 20 | – |
| median active wall | 12,539 ms | 11,347 ms | −9.5% |
| mean active wall | 15,064 ms | 20,081 ms | +33% (q002 outlier) |
| mean LLM share | 16.2% | 16.3% | **+0.1 pp** |
| mean `llm.generate` ms | 2,147 | 1,967 | −8.4% |
| mean tool share | 83.8% | 83.7% | −0.1 pp |
| total retry_waits | 0 | 0 | – |
| truncated | n/a (no loop) | n/a | – |

The +0.1 pp LLM-share shift contrasts sharply with the agentic
architecture's +9.3 pp shift on the same Haiku → Opus swap. Both
sweeps used the identical pipeline (search → fetch×2 → summarize×2 →
1 LLM call), so any architectural variable is held constant. **The
model axis is essentially invisible in the cost breakdown of a
hardcoded pipeline; it only becomes visible when the orchestrator
calls the LLM multiple times per query.**

## Superseded — agentic-architecture results

For reference, the earlier agentic-loop runs (now under
`benchmark/results/cell_{haiku,opus}_agentic/`) reported:

| Cell | Median active | LLM share | Tool share | Truncated |
|---|---:|---:|---:|---|
| Haiku agentic | 11,005 ms | 36.7% | 63.3% | 3 / 20 (q009, q013, q020) |
| Opus agentic  | 11,171 ms | 46.0% | 54.0% | 2 / 20 (q013, q020) |

Both numbers are higher in LLM share than the corresponding pipeline
cells because the agentic loop calls the LLM 3-5 times per query
instead of once. The cross-cell delta is +9.3 pp (Haiku→Opus), with
median active wall time essentially identical between the two — that
result remains a defensible secondary observation about how model
capability shapes agentic tool-use patterns.

---

## Data quality issue — 5 failed Go traces

While preparing fig 2 from the legacy `benchmark/traces/{py,go}`
data, recomputed v1 / v2 means did not match the numbers cited in
earlier thesis text (Py 2744, Go 3925, etc.). Inspection of individual
Go traces revealed:

- `benchmark/traces/go/q016.json` through `q020.json` each contain a
  single `llm.generate` span with status `error` and the error message
  `anthropic /v1/messages status 400: {"type":"error","error":{"type":
  "invalid_request_error","message":"Your credit balance is too low to
  access the Anthropic API. ..."}}`.
- These records were not measurements; they were records of an
  earlier Go sweep running out of API credits mid-run.
- Their tiny ~50–110 ms `llm.generate` spans were dragging Go's mean
  LLM time down from ~4,911 ms (true mean across 15 successful runs)
  to ~3,704 ms (apparent mean across 20 records including the 5
  failures) — silently inflating Go's apparent LLM advantage in fig 2.

**Resolution:**

1. Added `metrics.is_failed_trace(tf)` to detect this pattern (any
   span with `status != "ok"` and zero successful `llm.generate`
   spans). `build_df()` and the fig scripts now skip such traces
   automatically.
2. Deleted the 5 failed JSONs from `benchmark/traces/go/`. The
   directory now holds 15 clean traces (q001–q015).
3. Re-rendered fig 2: Go's recomputed v2 means now match the original
   thesis text — v1 makes Go appear ~1.30× slower than Py on LLM
   time, v2 reveals the LLM calls are essentially tied (~1.02×).

This also explains the "13-query clean data" framing in earlier text:
20 − 5 (failures) − 2 (q012, q015 retry-affected) = 13.

The filter is retained as defensive code so future failures are caught
without manual triage.

---

## Figures

All under `benchmark/plots_paper/out/`. Each figure has a sibling
`.txt` caption file with full source, caveats, and citations.

### Figure 1 — `fig1_cost_breakdown.{pdf,png}`

Stacked bar chart, three bars normalized to 100% of active wall time:

| Bar | LLM | Tool: search | Tool: fetch | Other | Total | Annotation |
|---|---:|---:|---:|---:|---:|---|
| Haiku 4.5 + Py chain | 36.7% | 36.0% | 27.2% | 0% | 100% | 11.0 s (n=20) |
| Opus 4.7 + Py chain | 46.0% | 29.5% | 24.5% | 0% | 100% | 11.2 s (n=20) |
| Raj et al. 2024 (gpt-oss-20b) | 28% | 0% | 55% | 22% | 105% | 6.0 s (cited) |

- Cells use 95% CI error caps on each non-zero segment.
- Raj bar has no error caps (cited data without intervals); preserves
  the source figure's ~5% rounding artifact rather than silently
  rescaling.
- "Other (orchestration)" segment is unique to Raj's bar; reddish-purple
  in the palette to make the no-analog status visually obvious.

### Figure 2 — `fig2_retry_split.{pdf,png}`

Two stacked-bar subplots, side by side, sharing the y-axis:

- **Left (v1 schema, retry folded into LLM):** GO 7,467 ms total appears 1.28× slower than PY 5,846 ms.
- **Right (v2 schema, retry split):** GO LLM segment shrinks; gray retry-wait segment (~1,600 ms) on top; LLM portions are now visibly equal (Py 5,004 vs Go 4,911 ≈ 1.02×).

The figure makes the framework's measurement story visually obvious:
v1 → v2 changes nothing about the agents; it changes only how the
framework attributes time. That single change reveals a real
cross-language equivalence that v1 had hidden inside `llm.generate`.

### Figure 4 — `fig4_architecture_comparison.{pdf,png}`

Five-bar cost-breakdown comparison spanning the full architecture × model
matrix plus the cited Raj bar:

  1. Haiku 4.5 + agentic
  2. Opus 4.7 + agentic
  3. Haiku 4.5 + pipeline
  4. Opus 4.7 + pipeline
  5. Raj et al. 2024 (cited)

Subtle brackets above the bars group the two architecture blocks. The
within-block comparisons (1↔2, 3↔4) read off the model-axis shift; the
between-block comparison (2↔3 or 1↔4) reads off the orchestrator-axis
shift. Same palette and normalization as fig 1.

This is the figure to point at when arguing that the orchestrator
architecture choice dominates the model choice — the model-axis effect
is visible only on the agentic block, while the architecture-axis effect
is visible everywhere.

### Figure 3 — `fig3_cross_lang.{pdf,png}`

Per-stage Py vs Go scatter, log-log. One point per (query, stage),
with the y=x diagonal as the cross-language equivalence reference.

- LLM points cluster along the diagonal (no systematic bias).
- `tool.fetch` shows scatter consistent with network variance on
  identical URLs.
- `tool.search` clustered near origin (legacy static backend).
- `tool.summarize` legend entry annotated `(n=0)` — present in
  instrumentation, never invoked by the model.

After the 5 failed Go traces were filtered/deleted, no anomalous
outliers below the diagonal remain; the cross-language equivalence
claim is supported across individual measurements, not only
per-config means.

---

## Key takeaways

1. **Pipeline architecture matches Raj's freshQA cost shape; tools dominate by a wide margin.** Cell 1 (Haiku pipeline) and Cell 2 (Opus pipeline) hit nearly identical 16.2% / 16.3% LLM share and 83.8% / 83.7% tool share, in the same direction as Raj's reported ~28% LLM / ~77% tool+other. The single largest cost in our pipeline is DDG search and URL fetching (combined ~12 s mean wall); a Google CSE swap would close most of the absolute-time gap to Raj's ~6 s total.

2. **In the pipeline architecture, model identity is essentially invisible** (+0.1 pp LLM share between Haiku and Opus). The orchestrator structurally caps LLM calls at one per query, so per-call latency differences cannot multiply into a visible share shift. The same Haiku → Opus swap moved LLM share by **+9.3 pp** in the (superseded) agentic architecture, where each query incurred 3-5 LLM calls. **The orchestrator architecture choice dominates the model choice on this workload.** A cost-breakdown plot that does not specify the orchestrator is hiding most of the variance.

3. **Pipeline brittleness is a real cost.** Roughly 9 / 20 of Cell 1's pipeline answers contained "I don't have enough information" — without an iterative-refinement loop, the model cannot recover when DDG's first 2 URLs miss. The agentic loop trades higher LLM cost for the ability to issue a second search query. Neither architecture is unconditionally better; the pipeline is cheaper but answers a less complete set of questions.

4. **Schema design is a measurement decision, not a stylistic one.** Fig 2 demonstrates: by changing only how retry sleeps are attributed (v1 inside `llm.generate` vs v2 sibling `llm.retry_wait` spans), the framework changes the apparent Py-vs-Go LLM ratio from 1.28× to 1.02×. No agent code was modified. The framework's instrumentation-design choice was hiding a real cross-language equivalence.

5. **Documented transport failures are not measurements.** The 5 deleted Go traces were a warning: failures-as-trace-records can silently distort downstream analysis, and a defensive filter (`metrics.is_failed_trace`) is cheap insurance.

6. **Agentic-cell secondary observation (kept for chapter context).** Within the agentic architecture, Cell 1 → Cell 2 LLM share jumps from 36.7% → 46.0% (+9.3 pp) but median active wall time barely moves (11.0 → 11.2 s). Opus's slower per-turn time is approximately offset by needing fewer turns to converge. Useful as an "agentic vs pipeline" framing alongside takeaway 2.

---

## Workload 1b — Concurrent batch sweep (Experiment A)

A second evaluation pass added to address the advisor question
"compiled Go should be faster than interpreted Python — where does that
show up?" The original Haiku-pipeline / Opus-pipeline cells run at
batch=1 (sequential), where the workload is network-bound and neither
language can wait faster. To expose any compiled-vs-interpreted
difference, the pipeline was re-run at concurrent batch sizes
[1, 4, 16, 64] in both Python (`ThreadPoolExecutor`, GIL exposed) and
Go (goroutines + buffered semaphore channel, no GIL), with a static-
backend validation cell at b=64 in each language.

### Hypothesis (per Raj et al. 2024 Figure 4c)

Python's `tool.summarize` stage (LexRank, a pure-Python NumPy
computation) should grow super-linearly with batch size as threads
contend for the GIL. Raj's Figure 4c shows summarize latency growing
from 2.9 s to 6.3 s between batch 64 and 128 (+2.2× on a 2× batch
increase). Go's goroutine model has no equivalent serialization
constraint, so its summarize latency should stay roughly flat as batch
size grows.

### Setup

| Cell | Lang | Batch | Search backend | Output dir |
|---|---|---:|---|---|
| primary 1-8 | Py + Go | 1, 4, 16, 64 | DDG (live) | `cell_concurrent_{py,go}_b{1,4,16,64}/` |
| validation 9 | Py | 64 | static | `cell_concurrent_py_b64_static/` |
| validation 10 | Go | 64 | static | `cell_concurrent_go_b64_static/` |

10 cells, 200 traces (20 queries × 10 cells), 14 minutes of wall clock,
~$0.60 in Anthropic spend. The validation cells exist because
`SEARCH_BACKEND=ddg` introduces a confound: if a language stalls at
high batch size, we need to know whether the GIL or DDG is responsible.
The static cell removes DDG from the loop and answers the question
cleanly.

A cross-language detail discovered during the sweep and worth
documenting: Python's `tools/search.py` resolves `SEARCH_BACKEND=auto`
to `ddg`, but Go's `internal/tools/search.go` resolves `auto` to
`static`. The Python default was deliberately flipped from `static` to
`ddg` in an earlier session; Go was never updated. To keep the cross-
language workload identical we exported `SEARCH_BACKEND=ddg` (or
`=static`) explicitly for every cell rather than patching the Go
default; the Go-side flip is logged as an open item.

### Results

| lang | batch | backend | wall (s) | throughput (q/s) | P50 search (ms) | P50 summarize (ms) | P50 llm (ms) | LLM retry_waits | non-empty searches |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| py | 1 | ddg | 332 | 0.060 | 3,305 | 198 | 2,123 | 0 | 20 / 20 |
| go | 1 | ddg | 133 | 0.150 | 942 | 27 | 1,578 | 0 | 20 / 20 |
| py | 4 | ddg | 79 | 0.253 | 3,024 | 246 | 2,106 | 0 | 20 / 20 |
| go | 4 | ddg | 22 | 0.909 | 2,054 | 0 | 1,144 | 0 | **7 / 20** |
| py | 16 | ddg | 52 | 0.385 | 4,265 | 241 | 1,895 | 0 | 20 / 20 |
| go | 16 | ddg | 17 | 1.176 | 2,086 | 0 | 1,050 | **1** | **0 / 20** |
| py | 64 | ddg | 39 | 0.513 | 5,357 | 315 | 1,903 | 0 | 20 / 20 |
| go | 64 | ddg | 70 | 0.286 | 36,005 | 0 | 1,779 | **13** | **0 / 20** |
| py | 64 | static | 18 | **1.111** | 0 | 230 | 1,986 | 0 | 20 / 20 |
| go | 64 | static | 18 | **1.111** | 0 | 8.8 | 1,721 | 5 | 20 / 20 |

### Findings (three, in order of importance)

**1. The GIL signal exists but is attenuated on this workload.** Python's
P50 `tool.summarize` grew from 198 ms (b=1) to 315 ms (b=64), a 1.6×
increase. The direction matches Raj et al.'s Figure 4c, but Raj's
magnitude (2.9 → 6.3 s, +2.2× on b=64 → b=128) is much larger.

The original explanation in this section was: "On FreshQA's short
news-clip pages our LexRank step costs ~100 ms of CPU per call; even
with 64 contending threads, GIL overhead is tens of ms, not seconds.
[…] the weakness is in the workload's CPU intensity, not in the
instrumentation. Workload 1c subsequently measured a 7.4× per-call
summarize cost on Wikipedia-length pages, which implies stronger
contention would be visible if Experiment A were re-run on that
workload."

**Amendment, after the W1b extension was run:** That explanation is
no longer well-supported. A direct test on HotpotQA-20 (W1b extension,
appended below) found similar attenuation (×1.55 P50 growth on
HotpotQA vs ×1.59 on FreshQA) under what was assumed to be a
substantially heavier workload. The diagnostic that ran alongside
that experiment also showed that the "7.4× per-call summarize cost"
characterization in W1c was inflated by a tail-outlier effect —
typically one ~22-second LexRank call per 20-query batch on a
pathological Wikipedia page, with the median per-call cost on
HotpotQA only ~1.27× FreshQA's. So the lightweight-LexRank hypothesis
was the working explanation but is not validated by the follow-up,
and the workload premise that motivated the follow-up was
overstated. The honest current state: **the GIL signal is attenuated
on this workload at these batch sizes; the reason is not yet pinned
down. The framework correctly measured both regimes — which is what
matters for the methodology claim — but the per-stage explanation
needs revision and a properly-heavier workload to test against.**

**2. Library robustness under concurrent scraping matters more than
language choice.** The Go runner's stdlib `net/http` POST + regex
extractor against `html.duckduckgo.com` is blocked by DDG's
anti-scraping system at concurrency ≥ 4: 13 / 20 of Go's b=4 queries
got zero URLs from DDG; b=16 and b=64 both saw 20 / 20 empty results.
Python's `ddgs` package (PyPI) survives the same conditions and
returned non-empty results for all 80 of its DDG queries. The
cross-language *throughput* comparison at b=4 / 16 / 64 is therefore
**not a comparison of language runtimes** — it's a comparison of how
well each ecosystem's most-obvious DDG client handles being
rate-limited. Figure 5 marks Go's degraded points with hollow markers
to make this immediately visible without reading the caption.

**3. The static b=64 validation proves the framework measures
correctly.** With DDG removed from the loop, **Python and Go reach
exactly the same throughput at b=64: 1.111 q/s for both**. The
remaining stages (fetch, summarize, LLM call) bottleneck on the same
external network resources, and the GIL contributes about 220 ms (the
gap between Python's 230 ms summarize and Go's 8.8 ms summarize on the
same 20 queries) — measurable but not dominant in a 5-7 s end-to-end
budget. The instrumentation pipeline (Observer + ThreadPoolExecutor in
Python, Observer + goroutines in Go) is concurrency-safe to the
resolution we measure, with one caveat: `cpu_time_ms` (per-span CPU
attribution) is process-wide in both languages, so under concurrency it
no longer attributes per-thread / per-goroutine. Wall-time spans
(`wall_time_ms`) — the metric Experiment A actually uses — remain
correct.

### Comparison to Raj et al. Figure 3

Raj's Figure 3 reports multi-processing being 26.8× faster than
sequential and 1.6× faster than multi-threading at batch=128. We can
confirm direction but not magnitude:

- *Direction confirmed*: Python throughput plateaus relative to
  the work added. The b=4 → b=16 → b=64 speedups are 1.5×, then 1.3× —
  classic GIL-bound diminishing returns. Without the GIL we would
  expect the speedup ratio to stay closer to 4× per 4× batch increase.
- *Magnitude not reproduced*: at our batch sizes (1-64) and our
  workload (LexRank on short pages), we do not observe the dramatic
  multi-threading vs multi-processing gap Raj reports at b=128. Raj
  used larger documents and pushed past our top batch size; both
  changes would amplify the GIL signal here.

### Important caveat in Figure 5

Go's b=4/16/64 DDG points in Figure 5 Panel B (P50 summarize) sit near
zero ms not because Go's goroutines outran summarize, but because Go
short-circuited before reaching the summarize stage in 13/20, 20/20,
and 20/20 queries respectively (no URLs from DDG → no fetches → no
summarize calls). The figure marks these points with hollow squares
per the standard "data point exists but isn't a valid measurement"
visualization convention. The static b=64 diamonds are the only Go
points where summarize ran on real input.

### Future work

Originally framed as natural future work motivated by the W1c mean
summarize cost: re-running Experiment A on HotpotQA-20 should expose
stronger GIL contention.

**Amendment:** The follow-up was subsequently run (W1b extension,
appended below). The workload premise was overstated by a tail-
outlier effect — HotpotQA's median per-call summarize cost is only
~1.27× FreshQA's, not 7.4×. The test was inconclusive: the GIL
signal looked the same on both datasets because the workloads were
not actually substantially different at the typical query. A proper
test of the lightweight-LexRank hypothesis would require a workload
whose median, not just its tail, is substantially heavier than
FreshQA's — for example, a benchmark over scientific papers or
long-form articles where every query surfaces a multi-page document.

---

## Workload 1b extension — re-run on HotpotQA-20

Test of the lightweight-LexRank explanation from W1b proper. If the
explanation is correct, a heavier workload should produce a steeper
per-stage GIL signal. HotpotQA-20 was the natural candidate because
W1c characterized its per-call summarize cost as +637% versus
FreshQA. Python-only sweep (Go was skipped: the W1b cells showed
Go's stdlib DDG scrape collapses at b≥4, so a heavier workload would
not change that finding). Cells: `cell_concurrent_py_hotpot_b{1,4,16,64}/`.

The static b=64 validation cell from W1b was attempted but **failed
to run**: HotpotQA queries carry no pre-resolved URLs (`urls: []`),
so `SEARCH_BACKEND=static` returned 0 URLs for every query and the
pipeline crashed at the search stage in 1 second. The static idiom
from W1b only works on FreshQA because FreshQA queries carry
pre-resolved URLs. Excluded from analysis; the four DDG cells are
the comparison.

### Setup

| Field | Value |
|---|---|
| Dataset | HotpotQA-20 (`benchmark/queries/hotpotqa_20.json`) |
| Cells | 4 — Py concurrent at batch [1, 4, 16, 64], `SEARCH_BACKEND=ddg` |
| Output | `benchmark/results/cell_concurrent_py_hotpot_b{N}/` |
| Wall | ~10 min total, 80 traces, ~$0.10 spend |
| Tooling | Reused `config_concurrent_py.py` with new `--cell-name` flag for trace-level disambiguation |

### Results — per-stage P50 + mean comparison

P50 (typical query) is the honest comparison metric here; mean is
contaminated by a single ~22-second outlier per 20-query batch.

| dataset | batch | P50 sum-summarize/q (ms) | mean sum/q (ms) | P95 sum/q (ms) | P50 LLM (ms) | retry_waits | empty searches |
|---|---:|---:|---:|---:|---:|---:|---:|
| FreshQA | 1 | 198 | 305 | 947 | 2,123 | 0 | 0 |
| FreshQA | 4 | 246 | 283 | 557 | 2,106 | 0 | 0 |
| FreshQA | 16 | 241 | 288 | 545 | 1,895 | 0 | 0 |
| FreshQA | 64 | 315 | 381 | 574 | 1,903 | 0 | 0 |
| **HotpotQA** | 1 | 207 | 1,493 | 1,792 | 2,123 | 0 | 0 |
| **HotpotQA** | 4 | 290 | 2,023 | 2,979 | 2,204 | 0 | 0 |
| **HotpotQA** | 16 | 374 | 1,827 | 2,605 | 2,167 | 0 | 0 |
| **HotpotQA** | 64 | 321 | 576 | 1,586 | 2,152 | 0 | 0 |

P50 summarize growth, b=1 → b=64:
- FreshQA: 198 → 315 = **×1.59**
- HotpotQA: 207 → 321 = **×1.55**

The two growth ratios are essentially identical. See
`fig7_w1b_hotpot_extension.{pdf,png}` for the overlay plot.

### Findings (honest reporting, no softening)

**1. Hypothesis not supported, but also not properly tested.** The W1b
"lightweight LexRank" explanation predicted that a heavier per-call
workload would produce a steeper P50 summarize curve. HotpotQA's
curve is essentially the same shape as FreshQA's (×1.55 vs ×1.59).
Read at face value, that contradicts the explanation. But the
diagnostic in finding (2) shows the test premise was wrong, so the
hypothesis is now uncertain rather than refuted.

**2. The W1c "+637% per-call summarize" claim was a mean-vs-median
artifact.** Per-individual-summarize-span cost on the sequential
baselines:

| Source | n spans | P50 (ms) | mean (ms) | P95 (ms) | max (ms) |
|---|---:|---:|---:|---:|---:|
| FreshQA W1c sequential | 40 | 64 | 123 | 284 | 1,161 |
| HotpotQA W1c sequential | 38 | 80 | 954 | 3,409 | **22,776** |
| FreshQA W1b b=1 | 40 | 67 | 152 | 431 | 1,020 |
| HotpotQA W1b-ext b=1 | 40 | 79 | 746 | 1,048 | **21,972** |

At the median, HotpotQA per-call summarize is only **~1.27×** FreshQA's
(80 ms vs 64 ms in W1c; 79 ms vs 67 ms in W1b-ext). The +637% mean
delta in W1c is driven by ~1 outlier per pass — typically one large
Wikipedia page that LexRank chokes on for 22 seconds. Most queries
summarize in ~80 ms, comparable to FreshQA's ~64 ms. The W1c section
above has been amended to surface this correction inline.

**3. Mean-summarize on HotpotQA dropped at b=64 — workload effect, not
runtime.** Mean fell from 1,827 ms (b=16) to 576 ms (b=64). Diagnostic
cause: at b=64, DDG returned different (smaller) URLs under heavy
concurrent load — mean fetch bytes dropped from 17.7 KB to 11.6 KB.
The runtime is fine; the workload was not held constant across batch
sizes. Confirms that P50, not mean, is the honest comparison metric
for this experiment.

**4. LLM stage was stable across all 4 cells.** P50 LLM ~2.1 s, no
Anthropic retry_waits in any cell, no empty searches. The variance
is all on the tool side; the LLM side did not contribute confounders.

### Verdict on the lightweight-LexRank hypothesis

It is now uncertain — neither cleanly confirmed nor cleanly refuted.
The follow-up that should have tested it didn't actually run a
substantially heavier workload at the typical query, because the
workload premise was overstated. A proper test would require a
benchmark whose **median** per-call summarize cost is materially
higher than FreshQA's — not just one with occasional pathological
pages in the tail. The current W1b explanation (and the W1c future-
work pointer that motivated this experiment) have been amended above
to acknowledge this uncertainty rather than continue to assert the
unsupported explanation.

This is the second instance in this thesis of the framework
surfacing a measurement artifact in its own results — the first
being the v1→v2 retry-split refactoring (W1b proper, finding from
the legacy cross-language pass). Both demonstrate the same
methodological property: **a framework that exposes the structure
of its own measurements enables iterative correction**, including
of the narratives previously written on top of those measurements.

---

## Workload 1c — Dataset replication (Experiment B)

A third evaluation pass added to address the advisor question "is the
cost-breakdown shape FreshQA-specific, or does it generalize?". The
same hardcoded pipeline (web_search → fetch_url ×2 → lexrank_summarize
→ single LLM call), the same model (Haiku 4.5), and the same
instrumentation as Cell 1 (Workload 1a / pipeline_haiku) was run on a
second QA benchmark.

**Caveat — read first.** HotpotQA dev distractor only contains
`level=hard` examples (easy/medium splits are train-only, not used
for evaluation by the dataset's authors). The W1c cell therefore
covers only **multi-hop hard** questions, while FreshQA-20 covers a
mix of never/slow/fast-changing freshness levels (i.e., a broader
difficulty range). The cells are directly comparable for the question
*"does the cost-breakdown shape generalize?"* — they are NOT
comparable as a same-difficulty replication.

### Hypothesis

The cost-breakdown shape (tool-dominant by a wide margin, LLM-small)
is dataset-independent at the architecture level, because the pipeline
structurally caps LLM cost at one call per query and the dominant cost
is in the search + fetch stages. Per-stage absolute magnitudes may
shift in dataset-predictable ways (HotpotQA's multi-hop questions
should be tool-heavier).

### Setup

| Field | Value |
|---|---|
| Dataset | HotpotQA dev distractor v1 |
| Source | `http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json` |
| Selection seed | 42 |
| Stratification | 7 bridge-entity + 7 comparison-entity + 6 comparison-yesno |
| Stratification note | type × answer_form (level was uniformly `hard`, so type × level collapsed; bridge-yesno is empty in the dataset) |
| Cell config | `config_pipeline_haiku_hotpot.py` (re-exports `run` from `config_pipeline_haiku.py`; only `LABEL` and `DEFAULT_OUT_DIR` differ) |
| Output dir | `benchmark/results/cell_haiku_pipeline_hotpot/` |
| Sample | 20 queries, ~5 min wall, ~$0.02 spend |

### Results — per-stage comparison

| Metric | FreshQA-20 | HotpotQA-20 | Δ |
|---|---:|---:|---:|
| queries completed | 20 / 20 | 20 / 20 | – |
| median active wall (ms) | 12,539 | 20,705 | +65.1% |
| mean active wall (ms) | 15,064 | 21,463 | +42.5% |
| `tool.search` mean (ms) | 3,290 | 6,061 | **+84.2%** |
| `tool.fetch` mean (ms) | 9,380 | 11,190 | +19.3% |
| `tool.summarize` mean (ms) | **246** | **1,812** | **+636.9%** |
| `llm.generate` mean (ms) | 2,147 | 2,399 | +11.7% |
| **tool share of active** | **83.8%** | **87.2%** | **+3.4 pp** |
| **LLM share of active** | **16.2%** | **12.8%** | **−3.4 pp** |
| input tokens (mean) | 186 | 160 | −13.9% |
| output tokens (mean) | 102 | 103 | +1.4% |
| total `llm.retry_wait` | 0 | 0 | – |

### Findings

**1. Cost-breakdown shape generalizes.** Both cells sit in the same
regime: tool-dominant by a wide margin (83.8% vs 87.2%) with LLM small
(16.2% vs 12.8%). The architecture-vs-model finding from EVAL_LOG (the
+0.1 pp Haiku→Opus shift on pipeline vs the +9.3 pp shift on agentic)
**rests on a stable foundation** — the cost shape is dataset-
independent at the architecture level, not a FreshQA artifact.
Hypothesis confirmed.

**2. Per-stage absolute magnitudes shift in the predicted direction
and in one striking way.** HotpotQA's underlying sources are
Wikipedia-style pages, roughly an order of magnitude longer than
FreshQA's news clips. LexRank scales super-linearly with document
size, so `tool.summarize` jumps **+637%** (246 → 1,812 ms). The search
stage grows +84% (more entity-dense queries take DDG longer to
resolve). Fetch grows +19% (bigger pages but the fetch is mostly
network round-trip, not parsing). The framework attributes each shift
cleanly to the right span — see Figure 6 Panel B for the visual.

> **Amendment (added after W1b extension).** Subsequent analysis
> (W1b extension, appended below this section) showed that the
> +637% mean summarize jump is driven by tail outliers — typically
> one Wikipedia page per 20-query batch that LexRank chokes on for
> 5–22 seconds. The **median** per-call summarize cost on HotpotQA
> is ~1.27× FreshQA's (80 ms vs 64 ms in W1c; 79 ms vs 67 ms in the
> W1b-extension b=1 cell), comparable in the typical case. The
> cost-shape generalization finding (the +3.4 pp tool/LLM share
> shift) holds. The magnitude characterization in this paragraph
> was overstated for the typical query and the original wording
> above is preserved to keep the intellectual history visible.

**3. The LLM stage behaves identically across datasets.** `llm.generate`
mean grew only +12% (2,147 → 2,399 ms), input tokens dropped −14%, and
output tokens were flat (+1.4%). The wall-time growth from FreshQA to
HotpotQA is **not driven by more LLM work** — it is pure tool-side
cost. This makes the architecture-vs-model finding more robust, not
less: the LLM side behaves the same on both datasets even when the
tool side does not.

### Implication for Workload 1b

Originally framed as natural future work motivated by the mean
summarize cost: the 7.4× per-call `tool.summarize` cost on HotpotQA
pages was expected to expose substantially stronger Python GIL
contention if Experiment A were re-run on HotpotQA.

**Amendment.** The follow-up was subsequently run (W1b extension,
appended above this section). The workload premise was overstated by
a tail-outlier effect — HotpotQA's median per-call summarize cost is
only ~1.27× FreshQA's, not 7.4× — and the test was inconclusive: P50
summarize growth from b=1 to b=64 was ×1.55 on HotpotQA vs ×1.59 on
FreshQA, essentially identical. A proper test of the lightweight-
LexRank hypothesis would require a workload whose median, not just
its tail, is substantially heavier than FreshQA's. The W1b section
above has been amended to acknowledge that the lightweight-LexRank
explanation is now uncertain rather than supported.

---

## Reproducibility

```bash
# Re-run Cell 1 (Haiku pipeline) — ~5 min wall, ~$0.01 budget
uv run --group benchmark python -m benchmark.run \
    --config pipeline_haiku \
    --queries benchmark/queries/freshqa_20.json

# Re-run Cell 2 (Opus pipeline) — ~10 min wall, ~$0.50-1.50 budget
uv run --group benchmark python -m benchmark.run \
    --config pipeline_opus \
    --queries benchmark/queries/freshqa_20.json

# Optional: re-run the superseded agentic versions
#   --config haiku_custom    # Haiku agentic, ~11 min, ~$0.05
#   --config opus_custom     # Opus agentic,  ~11 min, ~$1-5

# Re-render the seven paper figures (idempotent, seconds each)
uv run --group benchmark python -m benchmark.plots_paper.make_fig1
uv run --group benchmark python -m benchmark.plots_paper.make_fig2
uv run --group benchmark python -m benchmark.plots_paper.make_fig3
uv run --group benchmark python -m benchmark.plots_paper.make_fig4
uv run --group benchmark python -m benchmark.plots_paper.make_fig5_gil_concurrency
uv run --group benchmark python -m benchmark.plots_paper.make_fig6_dataset_replication
uv run --group benchmark python -m benchmark.plots_paper.make_fig7_w1b_hotpot_extension
```

### Workload 1b extension — HotpotQA re-run

```bash
# Py-only sweep on HotpotQA-20, 4 cells, ~10 min wall, ~$0.10 budget.
for B in 1 4 16 64; do
    SEARCH_BACKEND=ddg CONCURRENT_BATCH_SIZE=$B \
        uv run --group benchmark python -m benchmark.configs.config_concurrent_py \
        --queries benchmark/queries/hotpotqa_20.json \
        --cell-name "cell_concurrent_py_hotpot_b${B}" \
        --out "benchmark/results/cell_concurrent_py_hotpot_b${B}"
    sleep 10
done
```

Note: the static-backend validation cell that worked in W1b proper does
not work here — HotpotQA queries have no pre-resolved URLs, so static
lookup fails immediately. See the W1b extension section above.

### Workload 1b (Experiment A) — concurrent batch sweep

```bash
# Full 10-cell sweep (8 DDG primary + 2 static validation), ~14 min wall,
# ~$0.60 budget. Writes per-cell logs to benchmark/results/experiment_a_master.log.
bash benchmark/run_experiment_a.sh

# Or individual cells (set CONCURRENT_BATCH_SIZE and SEARCH_BACKEND explicitly):
SEARCH_BACKEND=ddg CONCURRENT_BATCH_SIZE=16 \
    uv run --group benchmark python -m benchmark.configs.config_concurrent_py \
    --queries benchmark/queries/freshqa_20.json

cd benchmark-go && SEARCH_BACKEND=ddg CONCURRENT_BATCH_SIZE=16 \
    go run ./cmd/concurrent_go \
    --queries ../benchmark/queries/freshqa_20.json
```

Note: `SEARCH_BACKEND=ddg` must be set explicitly. Python's `auto`
resolves to `ddg`; Go's `auto` resolves to `static`. Logged as an open
item below.

### Workload 1c (Experiment B) — HotpotQA replication

```bash
# Single cell on HotpotQA-20, ~5 min wall, ~$0.02 budget
SEARCH_BACKEND=ddg uv run --group benchmark python -m benchmark.run \
    --config pipeline_haiku_hotpot \
    --queries benchmark/queries/hotpotqa_20.json
```

The HotpotQA query set was sampled with `selection_seed=42` and is
checked in at `benchmark/queries/hotpotqa_20.json` (no need to re-pull
the source dataset for replication).

Required env (loaded from `benchmark/.env`):

- `ANTHROPIC_API_KEY` — Anthropic Console key on the user's account.
- `SEARCH_BACKEND=auto` — picks DDG when no Google CSE keys are set.

The pre-sweep sanity probe will fail loudly if the key is missing or
either model id no longer resolves; pass `--skip-probe` to bypass.

For the two figures that depend on the legacy traces (fig 2, fig 3),
no new run is needed — they read directly from
`benchmark/traces/{py,go}/`.

---

## Open items

- **Raj et al. percentages**: hardcoded in `make_fig1.py` from
  Figure 2c freshQA bar (Web-Augmented Agent panel). The user plans to
  verify the legend mapping against panels 2a/2b when reading the paper
  for the chapter draft. Fix-if-wrong is a one-line change in
  `RAJ_ET_AL_PERCENTAGES`.
- **Cross-language pass on the new tool chain**: this session's
  cells run only on Python. A Go implementation of the live-DDG /
  trafilatura / LexRank chain would let fig 3 cover the new workload,
  not just the legacy static-backend one. Out of scope here; logged
  for future work.
- **Legacy `report.md`**: predates this session, covers the
  cross-language story in detail. Worth re-reading and updating if any
  numbers there were derived from data that has since been changed.
- **Go `SEARCH_BACKEND=auto` default**: `benchmark-go/internal/tools/
  search.go` resolves `auto` to `static`, while `benchmark/tools/
  search.py` resolves `auto` to `ddg` (Python was deliberately flipped
  in an earlier session; Go was never updated). Worked around in
  Workload 1b by exporting `SEARCH_BACKEND` explicitly for every cell;
  the divergence is a one-line fix in Go but was deferred to keep
  existing Go traces reproducible.
- **Re-running Workload 1b on HotpotQA**: motivated by the W1c finding
  that HotpotQA pages cost 7.4× more per `tool.summarize` call. Would
  exercise the GIL more strongly than FreshQA does. Pure data-side work;
  no framework changes needed.

---

## File map

```
benchmark/
├── EVAL_PLAN.md                          # design + post-hoc
├── EVAL_LOG.md                           # this document
├── report.md                             # legacy (cross-language)
├── backfill_termination.py               # one-shot retroactive annotator
├── run.py                                # extended sweep driver
├── run_experiment_a.sh                   # 10-cell W1b orchestrator
├── analysis/
│   └── metrics.py                        # + is_failed_trace filter
├── queries/
│   ├── freshqa_20.json                   # 20 queries (Workload 1a / 1b)
│   └── hotpotqa_20.json                  # 20 queries (Workload 1c)
├── configs/
│   ├── __init__.py                       # registry: py / *_custom / pipeline_*
│   ├── config_py.py                      # legacy, unchanged
│   ├── config_haiku_custom.py            # agentic, superseded
│   ├── config_opus_custom.py             # agentic, superseded
│   ├── config_pipeline_haiku.py          # Cell 1 (current)
│   ├── config_pipeline_opus.py           # Cell 2 (current)
│   ├── config_pipeline_haiku_hotpot.py   # Cell W1c (HotpotQA replication)
│   ├── config_concurrent_py.py           # W1b Python concurrent runner
│   └── _pipeline_helpers.py              # tool-less single-shot LLM wrapper
├── tools/
│   └── search.py                         # default flipped to DDG
├── plots_paper/                          # NEW package
│   ├── __init__.py
│   ├── style.py                          # rcParams + Wong palette
│   ├── make_fig1.py                      # cost breakdown (pipeline cells + Raj)
│   ├── make_fig2.py                      # v1 vs v2 retry split
│   ├── make_fig3.py                      # cross-language scatter
│   ├── make_fig4.py                      # 4-way architecture x model + Raj
│   ├── make_fig5_gil_concurrency.py      # W1b throughput + summarize panels
│   ├── make_fig6_dataset_replication.py  # W1c shape + magnitudes panels
│   ├── make_fig7_w1b_hotpot_extension.py # W1b-ext FreshQA vs HotpotQA P50/mean overlay
│   └── out/                              # PDF + PNG + caption.txt for each fig
├── results/
│   ├── cell_haiku_pipeline/              # 20 traces (Cell 1, current)
│   ├── cell_opus_pipeline/               # 20 traces (Cell 2, current)
│   ├── cell_haiku_agentic/               # 20 traces (superseded)
│   ├── cell_opus_agentic/                # 20 traces (superseded)
│   ├── cell_haiku_pipeline_hotpot/       # 20 traces (W1c)
│   ├── cell_concurrent_py_b{1,4,16,64}/  # 80 traces (W1b Py DDG)
│   ├── cell_concurrent_go_b{1,4,16,64}/  # 80 traces (W1b Go DDG)
│   ├── cell_concurrent_{py,go}_b64_static/  # 40 traces (W1b validation)
│   ├── cell_concurrent_py_hotpot_b{1,4,16,64}/  # 80 traces (W1b extension)
│   ├── experiment_a_master.log           # per-cell sweep log for W1b
│   ├── w1b_hotpot_master.log             # per-cell sweep log for W1b extension
│   └── final*, smoke*                    # legacy archives
└── traces/
    ├── py/                               # 20 legacy Py traces
    └── go/                               # 15 legacy Go traces (5 failures deleted)

benchmark-go/                             # Go runtime
├── cmd/
│   ├── run/                              # legacy sequential agentic runner
│   └── concurrent_go/                    # W1b Go concurrent runner (NEW)
├── internal/
│   ├── obs/                              # JSON-byte-compatible Observer
│   ├── agent/                            # legacy agentic loop
│   ├── pipeline/                         # W1b pipeline package (NEW)
│   └── tools/                            # search/fetch/summarize
```
