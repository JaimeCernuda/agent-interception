"""Workload 1c (Experiment B): Haiku 4.5 + hardcoded pipeline on HotpotQA-20.

Identical to config_pipeline_haiku.py in every respect except the trace label
and the default output directory. Both cells run the same pipeline (web_search
-> fetch_url x2 -> lexrank_summarize x2 -> single LLM call), so per-stage cost
shares between the two cells answer the question: does the cost-breakdown
shape we report on FreshQA generalize to a second QA benchmark?

The query set is benchmark/queries/hotpotqa_20.json (HotpotQA dev distractor,
20 queries stratified 7 bridge-entity + 7 comparison-entity + 6 comparison-yesno,
selection_seed=42). Note: HotpotQA dev distractor is uniformly level=hard;
the cell therefore covers only hard multi-hop questions, while FreshQA-20
covers a mix of freshness levels. The cells are directly comparable for
cost-breakdown shape, NOT for difficulty distribution.
"""
from __future__ import annotations

from pathlib import Path

# Re-export the canonical pipeline run function. Sharing the implementation
# guarantees the two cells differ only in the query set and output dir, so any
# cost-breakdown difference is attributable to the data, not the code.
from benchmark.configs.config_pipeline_haiku import run  # noqa: F401

LABEL = "cell_haiku_pipeline_hotpot"
DEFAULT_OUT_DIR = Path("benchmark/results/cell_haiku_pipeline_hotpot")
