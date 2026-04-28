"""Cell 2: Claude Opus 4.7 + custom Python tool chain (live DDG search).

One of the two cells defined in benchmark/EVAL_PLAN.md. Held constant with
Cell 1 on tool chain (live DDG + trafilatura + LexRank), retry policy,
max_turns, and tool-use protocol; differs from Cell 1 only in model identifier.

Differs from config_py.py only in:
  - default model string ("claude-opus-4-7")
  - default inter-turn pause (5 s vs 15 s; see EVAL_PLAN.md)
  - LABEL and DEFAULT_OUT_DIR constants

The model id "claude-opus-4-7" is the alias for the latest Opus 4.7 snapshot.
If the alias does not resolve at run time, override via ANTHROPIC_MODEL with
a dated form. The runner does a 1-token sanity probe before the full sweep
so a wrong id fails fast rather than 20 queries deep.
"""
from __future__ import annotations

import os
import time as _time
from pathlib import Path

import anthropic

from benchmark.configs.config_py import (
    TOOLS,
    _MAX_TURNS,
    _call_with_429_retry,
    _dispatch_search,
)
from benchmark.obs import Observer
from benchmark.tools import fetch_url, lexrank_summarize

_DEFAULT_MODEL = "claude-opus-4-7"
_DEFAULT_INTER_TURN_PAUSE_S = 5.0

LABEL = "cell_opus_custom"
DEFAULT_OUT_DIR = Path("benchmark/results/cell_opus_custom")


def run(query: dict, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=0,
    )

    dispatch = {
        "web_search": lambda kwargs: _dispatch_search(kwargs, obs, query["query_id"]),
        "fetch_url": lambda kwargs: fetch_url(kwargs["url"], obs),
        "summarize": lambda kwargs: lexrank_summarize(
            kwargs["text"], obs, n_sentences=int(kwargs.get("n_sentences", 1))
        ),
    }

    messages: list[dict] = [{"role": "user", "content": query["question"]}]
    final_answer = ""
    pause_s = float(
        os.environ.get("ANTHROPIC_INTER_TURN_PAUSE", _DEFAULT_INTER_TURN_PAUSE_S)
    )

    with obs.root(query_text=query["question"]) as root_handle:
        terminated_reason = "max_turns"
        last_stop_reason = ""
        for _turn in range(_MAX_TURNS):
            if _turn > 0 and pause_s > 0:
                _time.sleep(pause_s)
            resp = _call_with_429_retry(client, model, messages, TOOLS, obs)

            messages.append({"role": "assistant", "content": resp.content})

            text_chunks = [
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ]
            if text_chunks:
                final_answer = "\n".join(text_chunks)

            last_stop_reason = resp.stop_reason or ""
            if resp.stop_reason != "tool_use":
                terminated_reason = "natural"
                break

            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                try:
                    out = dispatch[block.name](block.input)
                    content_str = out if isinstance(out, str) else str(out)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content_str,
                        }
                    )
                except Exception as e:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Tool error: {e}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        root_handle.set("agent.terminated_reason", terminated_reason)
        root_handle.set("agent.truncated", terminated_reason == "max_turns")
        root_handle.set("agent.last_stop_reason", last_stop_reason)
        root_handle.set("agent.max_turns", _MAX_TURNS)
        return final_answer
