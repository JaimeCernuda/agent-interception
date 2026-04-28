"""Cell 2 (pipeline): Claude Opus 4.7 + hardcoded LangChain-style pipeline.

Identical to config_pipeline_haiku.py except for the model identifier.
See that module's docstring for the pipeline definition and the rationale
for the architecture switch.
"""
from __future__ import annotations

import os
from pathlib import Path

import anthropic

from benchmark.configs._pipeline_helpers import pipeline_llm_call
from benchmark.obs import Observer
from benchmark.tools import fetch_url, lexrank_summarize, web_search

_DEFAULT_MODEL = "claude-opus-4-7"
_MAX_URLS = 2
_PROMPT_TEMPLATE = "Based on these summaries, answer: {question}\n\n{summaries}"

LABEL = "cell_opus_pipeline"
DEFAULT_OUT_DIR = Path("benchmark/results/cell_opus_pipeline")


def run(query: dict, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=0,
    )

    with obs.root(query_text=query["question"]) as root_handle:
        urls = web_search(query["question"], obs, query_id=query["query_id"])

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

        summaries: list[str] = []
        for txt in texts:
            try:
                summaries.append(lexrank_summarize(txt, obs, n_sentences=1))
            except Exception:
                continue

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
