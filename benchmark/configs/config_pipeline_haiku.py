"""Cell 1 (pipeline): Claude Haiku 4.5 + hardcoded LangChain-style pipeline.

Replaces the earlier agentic-loop config that lived under the same cell name.
The agentic data is preserved under benchmark/results/cell_haiku_agentic/ for
forensic comparison.

Pipeline (mirrors Raj et al. 2024, langchain/orchestrator.py):

    1. web_search(query)            -> up to 10 URLs
    2. fetch_url(urls[:2])          -> 2 page texts (sequential, skip on error)
    3. lexrank_summarize(each text) -> 1 sentence per page
    4. llm.generate(prompt + sums)  -> single LLM call, no tools=, no system

The LLM is called exactly once per query. There is no agent loop, no
tool-use protocol, no max_turns. agent.terminated_reason is always
"natural"; agent.truncated is always False; agent.architecture =
"pipeline" so analysis code can distinguish pipeline traces from
agentic ones.
"""
from __future__ import annotations

import os
from pathlib import Path

import anthropic

from benchmark.configs._pipeline_helpers import pipeline_llm_call
from benchmark.obs import Observer
from benchmark.tools import fetch_url, lexrank_summarize, web_search

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_URLS = 2  # matches Raj's `if len(texts) >= 2: break`
_PROMPT_TEMPLATE = "Based on these summaries, answer: {question}\n\n{summaries}"

LABEL = "cell_haiku_pipeline"
DEFAULT_OUT_DIR = Path("benchmark/results/cell_haiku_pipeline")


def run(query: dict, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=0,
    )

    with obs.root(query_text=query["question"]) as root_handle:
        # 1. Web search
        urls = web_search(query["question"], obs, query_id=query["query_id"])

        # 2. Fetch up to _MAX_URLS pages, sequentially. Mirrors Raj's behavior:
        # walk the URL list and take the first N that successfully return text;
        # silently skip fetch errors (they emit a tool.fetch span with status=error
        # via the Observer, so the failure is recorded but does not abort the run).
        texts: list[str] = []
        for url in urls:
            if len(texts) >= _MAX_URLS:
                break
            try:
                txt = fetch_url(url, obs)
            except Exception:
                continue
            if txt:
                texts.append(txt)

        # 3. Summarize each fetched page (LexRank, 1 sentence). Same skip-on-error
        # pattern.
        summaries: list[str] = []
        for txt in texts:
            try:
                summaries.append(lexrank_summarize(txt, obs, n_sentences=1))
            except Exception:
                continue

        # 4. Single LLM call. No tools, no system prompt — matches Raj's
        # final_answer node exactly.
        prompt = _PROMPT_TEMPLATE.format(
            question=query["question"],
            summaries="\n\n".join(summaries),
        )
        resp = pipeline_llm_call(client, model, prompt, obs)
        text_chunks = [
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ]
        final_answer = "\n".join(text_chunks)

        root_handle.set("agent.architecture", "pipeline")
        root_handle.set("agent.terminated_reason", "natural")
        root_handle.set("agent.truncated", False)
        root_handle.set("agent.last_stop_reason", resp.stop_reason or "")
        root_handle.set("agent.num_urls_fetched", len(texts))
        root_handle.set("agent.num_summaries", len(summaries))
        return final_answer
