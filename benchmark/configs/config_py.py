"""Config Py: Claude Sonnet 4.5 + Python tool implementations, native tool-use protocol.

The Python side of the Py-vs-Go cross-language comparison. LLM and tool-use
protocol are held constant; only the tool-runtime language differs from Config Go
(which lives in benchmark-go/).

Env:
  ANTHROPIC_API_KEY=<key>
  ANTHROPIC_MODEL=claude-sonnet-4-5  (optional override)

Observability note: each model turn is one llm.generate span; each tool invocation
(executed locally on tool_use blocks) is one tool.* span. parse_error stays False
here because there is no text parsing - Claude emits structured tool_use blocks.
"""
from __future__ import annotations

import os
import time as _time

import anthropic

from benchmark.obs import Observer
from benchmark.tools import fetch_url, lexrank_summarize, web_search

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TURNS = 10  # safety cap; normal runs should finish in <=5
# Inter-turn pause to stay under Anthropic's 5 rpm rate limit.
# 15s -> at most 4 calls/min per query. Overridable via ANTHROPIC_INTER_TURN_PAUSE.
_DEFAULT_INTER_TURN_PAUSE_S = 15.0

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the web for pages relevant to a query. "
            "Returns a list of URLs (up to 10). Use this first to find sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch the readable plain-text content of a URL. "
            "Use on URLs returned by web_search. Fetch at most 2-3 pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "summarize",
        "description": (
            "Run LexRank extractive summarization on a text blob, "
            "returning the n most salient sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "n_sentences": {"type": "integer", "default": 1},
            },
            "required": ["text"],
        },
    },
]


SYSTEM_PROMPT = (
    "You are a web-augmented question-answering assistant. "
    "To answer the user's question, use the provided tools: "
    "first search the web, then fetch up to 2 URLs, then summarize each, "
    "then produce a concise final answer citing the summaries. "
    "Stop as soon as you have enough information."
)


def run(query: dict, obs: Observer) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    # max_retries=0: disable the SDK's internal retry loop so our own retry
    # logic (with explicit llm.retry_wait spans) is the only source of retries.
    # This keeps Py and Go structurally equivalent in how retries show up.
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
    pause_s = float(os.environ.get("ANTHROPIC_INTER_TURN_PAUSE", _DEFAULT_INTER_TURN_PAUSE_S))

    with obs.root(query_text=query["question"]):
        for _turn in range(_MAX_TURNS):
            if _turn > 0 and pause_s > 0:
                _time.sleep(pause_s)
            resp = _call_with_429_retry(client, model, messages, TOOLS, obs)

            # Append assistant message
            messages.append({"role": "assistant", "content": resp.content})

            # Extract any text the model produced this turn
            text_chunks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            if text_chunks:
                final_answer = "\n".join(text_chunks)

            if resp.stop_reason != "tool_use":
                break

            # Execute each tool_use block, feed results back
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                try:
                    out = dispatch[block.name](block.input)
                    content_str = out if isinstance(out, str) else str(out)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": content_str}
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

        return final_answer


def _call_with_429_retry(client, model, messages, tools, obs: Observer, max_retries: int = 4):
    """Honor Retry-After on 429; fall back to ~12s exponential. Mirrors Go side.

    Each HTTP attempt gets its own llm.generate span. Between attempts, a
    llm.retry_wait span measures the backoff sleep. So llm.generate wall time
    reflects only the actual API call, not retry pauses.
    """
    for attempt in range(max_retries + 1):
        with obs.span(
            "llm.generate",
            **{
                "llm.model": model,
                "llm.provider": "anthropic",
                "llm.parse_error": False,
                "llm.attempt": attempt,
            },
        ) as span:
            try:
                resp = client.messages.create(
                    model=model,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                    max_tokens=1024,
                )
                span.set("llm.input_tokens", int(resp.usage.input_tokens))
                span.set("llm.output_tokens", int(resp.usage.output_tokens))
                span.set("llm.stop_reason", resp.stop_reason or "")
                return resp
            except anthropic.RateLimitError as e:
                span.set("llm.rate_limited", True)
                span.set("llm.status_code", 429)
                if attempt == max_retries:
                    raise
                rate_limit_error = e
        # Outside the llm.generate span: decide the wait and emit llm.retry_wait.
        wait = 12.0 + 6.0 * attempt
        retry_after = getattr(getattr(rate_limit_error, "response", None), "headers", {}).get(
            "retry-after"
        )
        if retry_after:
            try:
                wait = float(retry_after)
            except (ValueError, TypeError):
                pass
        print(f"  429 rate-limited; sleeping {wait:.1f}s before retry {attempt + 1}")
        with obs.span(
            "llm.retry_wait",
            **{
                "llm.retry_after_s": wait,
                "llm.retry_attempt": attempt + 1,
                "llm.retry_trigger": "http_429",
            },
        ):
            _time.sleep(wait)
    raise RuntimeError("unreachable")


def _dispatch_search(kwargs: dict, obs: Observer, query_id: str):
    # If the model passes its own query string, trust it but tag span with our query_id
    # for static-backend lookup.
    return web_search(kwargs["query"], obs, query_id=query_id)
