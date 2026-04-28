# Evaluation Plan

This document fixes the experimental design for the evaluation chapter of the
thesis. It is the authoritative source for which cells are run, which numbers
are cited from prior work, and which existing data is used.

## Architecture: hardcoded pipeline (matches Raj et al.)

The cells implement the same fixed-pipeline architecture as Raj et al.
2024's LangChain orchestrator (`external/cpu-centric-agentic-ai/langchain/
orchestrator.py`):

    web_search(query)            -> up to 10 URLs
    fetch_url(urls[:2])          -> 2 page texts (sequential, skip on error)
    lexrank_summarize(each text) -> 1 sentence per page
    llm.generate(prompt + sums)  -> single LLM call, no tools, no system

The LLM is called **exactly once per query**. There is no agent loop,
no tool-use protocol, no `max_turns`. This makes the cells directly
comparable to the freshQA bar of Raj et al. Figure 2.

An earlier version of this evaluation used an agentic tool-use loop where
the model decided each turn which tool to call. That data is preserved
under `benchmark/results/cell_{haiku,opus}_agentic/` for forensic
comparison but is **no longer the headline architecture** — it answered a
different question (how does an agentic loop spend time?) than the one
the chapter is built around (how does the LangChain-style pipeline cost
breakdown shift across model speeds?).

## Design

A 1D comparison along the model-speed axis, holding the pipeline fixed:
live DDG search, trafilatura fetch, LexRank one-sentence summarization,
and a single Anthropic LLM call to compose the final answer. The two
cells differ only in the model identifier.

### Cell 1 — Haiku 4.5 + pipeline

| Field | Value |
|---|---|
| Model | `claude-haiku-4-5-20251001` |
| Architecture | hardcoded pipeline (see top of doc) |
| Search backend | `ddg` (resolved via `SEARCH_BACKEND=auto` with no Google CSE keys present) |
| Number of URLs fetched | up to 2 (matches Raj's `if len(texts) >= 2: break`) |
| Summarizer | sumy LexRank, n=1 sentence per page |
| LLM call | single, no tools, no system prompt; user prompt is `"Based on these summaries, answer: {q}\n\n{summaries}"` |
| Retry policy | `max_retries=4`, honor `Retry-After` on 429; same shape as the agentic configs (see `_pipeline_helpers.py`) |
| Output | `benchmark/results/cell_haiku_pipeline/` |
| Trace label | `cell_haiku_pipeline` |
| Hypothesis | LLM share of active wall time is small (≤ 30%) and tool share dominates, since the LLM is called once but the pipeline runs search+fetch×2+summarize×2 every query |

### Cell 2 — Opus 4.7 + pipeline

| Field | Value |
|---|---|
| Model | `claude-opus-4-7` |
| Architecture | identical to Cell 1 |
| Search backend | `ddg` |
| Number of URLs fetched | up to 2 |
| Summarizer | identical to Cell 1 |
| LLM call | single, identical prompt template |
| Retry policy | identical to Cell 1 |
| Output | `benchmark/results/cell_opus_pipeline/` |
| Trace label | `cell_opus_pipeline` |
| Hypothesis | LLM share rises modestly relative to Cell 1 (Opus is slower per token) but stays well under 50%; tool share still dominates because the pipeline structurally caps LLM time at one call |

### External comparison — Raj et al. 2024, Figure 2 (LangChain panel, freshQA bar)

LangChain orchestration, vLLM-served `gpt-oss-20b`, Python tool chain
structurally similar to the cells above. The exact percentages from
Figure 2c are not reproduced from memory in this doc to avoid fabricated
digits; the `fig1_cost_breakdown.py` script declares:

```
RAJ_ET_AL_PERCENTAGES = TBD  # Anna to insert from Figure 2c before Figure 1 renders
```

and the script raises a clear error rather than rendering with placeholder
data. This row is **cited, not run**, so no error bars are drawn for it.

A caveat will appear in the Figure 1 caption: Raj et al.'s instrumentation
does not separate retry backoffs into a sibling span, so their reported
"LLM share" includes retry sleeps, whereas ours excludes them. This means
their bar may understate true model-call time or, equivalently, their
"LLM share of active time" is not strictly comparable to ours.
The asymmetry is noted in the caption rather than corrected silently.

The point of including this row is twofold:

1. It anchors the absolute level of the tool share in a published agentic
   benchmark, so a reader who is unfamiliar with FreshQA can see that the
   per-stage shape we report is consistent with the broader literature.
2. It illustrates the model-speed effect: a 20B open-weights model served
   on local GPU pushes LLM share *down* (and tool share *up*) relative to
   a fast hosted model, which is the expected direction.

## Sample

| Field | Value |
|---|---|
| Dataset | `benchmark/queries/freshqa_20.json` (FreshQA April 21 2025, TEST split, false-premise filtered) |
| Stratification | 4 one-hop-never, 5 one-hop-slow, 4 one-hop-fast, 4 multi-hop-slow, 3 multi-hop-fast (already fixed in the file) |
| Selection seed | 42 |
| Queries per cell | 20 (full subset) |
| Pacing | ≥ 5 s sleep between queries, plus the 15 s inter-turn pause inside each query |

## Note on inter-turn pause

The agentic configs (now superseded) used a 5 s inter-turn pause; the
pipeline configs do not have a turn loop, so this knob no longer applies.
Inter-query pacing remains at 5 s (`run.py --sleep 5` default) for both
DDG and Anthropic rate-limit safety.

## What stays out

- **Native Anthropic `web_search_20250305`** is not used in either cell.
  The 2×2 design that would have included it has been narrowed to a 1D
  model-axis comparison with the tool chain held constant.
- **The Go implementation of the same chain (`benchmark-go/`)** is not part
  of this evaluation. Go traces under `benchmark/traces/go/` are used only
  by Figure 2 (v1-vs-v2 schema) and Figure 3 (per-stage cross-language
  scatter), which describe the framework's measurement story, not the
  model-speed story.

## Historical traces

The existing traces under `benchmark/traces/py/` and `benchmark/traces/go/`
were produced with `SEARCH_BACKEND=static`, which served pre-resolved URLs
from `freshqa_20.json` rather than performing live web search. Those
traces are kept for reproducibility of earlier figures and for the
v1-vs-v2 schema demonstration. They are explicitly **not** used as Cell 1
data: the cost-breakdown figure draws Cell 1 from the new live-DDG run
under `benchmark/results/cell_haiku_custom/`. The static-backend timings
(~0.08 ms per `tool.search` span) would otherwise produce a misleading
near-zero search bar.

### Known data quality issue: 5 failed Go traces

`benchmark/traces/go/q016.json` through `q020.json` are records of HTTP
400 "credit balance too low" failures from a Go sweep that ran out of
Anthropic API credits mid-run. Each contains a single failed
`llm.generate` span with status `error` and no successful completion.
They are not measurements; they are records of a transport failure.

`benchmark.analysis.metrics.is_failed_trace(tf)` returns True for these,
and `build_df(...)` plus the Fig 2 / Fig 3 plot scripts now skip them
automatically. With the filter, the v1-vs-v2 comparison uses **n = 15
clean Go queries vs n = 20 Py queries**; the queries q016–q020 are
absent from both sides of fig 3 (they have no usable Go counterpart).

This explains the "13 clean queries" framing in earlier thesis text:
20 − 5 (failures) − 2 (q012, q015 retry-affected) = 13.

## Reporting

For each cell the run script prints, on completion, a one-row summary
containing:

- queries completed (out of 20)
- median active latency in milliseconds (active = wall time minus all
  `llm.retry_wait` spans)
- mean LLM share of active time
- mean tool share of active time (sum across `tool.search`, `tool.fetch`,
  `tool.summarize`)
- total `llm.retry_wait` count across the cell

The same numbers are recomputed from the trace JSONs by the plotting
scripts, so the printed table is for sanity-checking, not for citation.

## Post-hoc observations

These notes are added after a cell runs. They are deliberately appended
rather than back-edited into the §Design hypotheses, so the gap between
predicted and observed remains visible to a future reader.

### Cell 1 (Haiku 4.5 + pipeline) — observed

```
queries completed         20 / 20
median active_latency_ms    12,539
mean LLM share of active       16.2%
mean tool share of active      83.8%
total llm.retry_wait               0
truncated                      n/a (no loop)
```

- **LLM share of active time: 16.2%**, well within the ≤30% hypothesis. Tool share dominates as predicted.
- Decomposition (means): `tool.search` 3,290 ms (DDG), `tool.fetch ×2` 9,380 ms total, `tool.summarize ×2` 246 ms total, `llm.generate ×1` 2,147 ms.
- DDG search and URL fetching together account for ~84% of active time. LLM is small because it fires only once per query.
- Answer-quality side effect: ~9 / 20 answers contain "I don't have enough information" or similar. Pipeline cannot iterate when the first 2 URLs miss; this is structural, not a bug.
- One sumy LexRank `RuntimeWarning: invalid value encountered in divide` from a degenerate normalization on one document. Output unaffected; flagged for awareness.

### Cell 2 (Opus 4.7 + pipeline) — observed

```
queries completed         20 / 20
median active_latency_ms    11,347
mean LLM share of active       16.3%
mean tool share of active      83.7%
total llm.retry_wait               0
truncated                      n/a (no loop)
```

- **LLM share of active time: 16.3%**, essentially identical to Cell 1. Hypothesis ("LLM share rises modestly") is **not confirmed** — Opus does not raise LLM share on this architecture.
- Decomposition (means): `tool.search` 3,420 ms, `tool.fetch ×2` 7,178 ms, `tool.summarize ×2` 7,516 ms (skewed; see below), `llm.generate ×1` 1,967 ms.
- Mean LLM time is **slightly lower** for Opus than Haiku (1,967 vs 2,147 ms). Opus appears to be similarly fast or faster on this small-prompt single-shot composition task. Counterintuitive but consistent across all 20 queries (range 1,223–3,117 vs 1,598–3,122).
- One pathological query (q002 "Who wrote Orientalism?") accumulated 145 s of `tool.summarize` time on a 2.6 MB HTML document that DDG happened to return. This single outlier dragged the per-cell mean active wall up to ~20 s, but the median (11.3 s) is in line with Cell 1.
- DDG non-determinism: between cells, the same query can receive different URLs because DDG does not return stable results. q002 is the most visible artifact of this.

### Side-by-side (pipeline cells)

| Metric | Cell 1 (Haiku) | Cell 2 (Opus) | Δ |
|---|---:|---:|---:|
| queries completed | 20 / 20 | 20 / 20 | – |
| median active wall | 12,539 ms | 11,347 ms | −9.5% |
| mean active wall | 15,064 ms | 20,081 ms | +33% (q002 outlier) |
| mean LLM share | 16.2% | 16.3% | **+0.1 pp** |
| mean LLM ms | 2,147 | 1,967 | −8.4% |
| mean tool share | 83.8% | 83.7% | −0.1 pp |
| total retry_waits | 0 | 0 | – |

**Headline finding (pipeline)**: model identity is essentially invisible
in the cost breakdown when the orchestrator caps LLM calls at exactly
one per query. Compare against the agentic architecture, where the
same Haiku → Opus swap shifted LLM share by +9.3 pp because each
query had 3-5 LLM calls instead of one. **The architecture choice
dominates the model choice on this workload.**

### Superseded — agentic-architecture cells

The earlier agentic-loop runs (data preserved under
`benchmark/results/cell_{haiku,opus}_agentic/`) reported:

| Cell | Median active | LLM share | Tool share | Truncated |
|---|---:|---:|---:|---|
| Haiku agentic | 11,005 ms | 36.7% | 63.3% | 3 / 20 |
| Opus agentic  | 11,171 ms | 46.0% | 54.0% | 2 / 20 |

Those numbers are not comparable to Raj et al. because the agentic loop
issues 3–5 LLM calls per query, while Raj's pipeline issues exactly one.
The headline observation from that data — convergent total wall time
despite a +9.3 pp LLM-share shift — remains an interesting secondary
result and is mentioned in the chapter's "agentic vs pipeline"
subsection, but it is not the figure-1 story.

