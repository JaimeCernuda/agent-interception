"""Shared helper for the pipeline configs (config_pipeline_haiku.py,
config_pipeline_opus.py).

Mirrors `_call_with_429_retry` in config_py.py but for a tool-less,
single-shot LLM call: no tools, no system prompt. Each HTTP attempt is a
distinct llm.generate span; backoff sleeps are llm.retry_wait siblings.
This matches the span shape of the agentic configs so figures and metrics
can consume both without special-casing.

config_py.py is intentionally not modified — the agentic helper there
keeps its tools/system parameters; this helper is the strictly simpler
twin used by the pipeline configs only.
"""
from __future__ import annotations

import time as _time

import anthropic

from benchmark.obs import Observer


def pipeline_llm_call(
    client: anthropic.Anthropic,
    model: str,
    prompt: str,
    obs: Observer,
    max_retries: int = 4,
    max_tokens: int = 1024,
):
    """Single LLM call with 429 retry. No tools, no system prompt.

    Matches Raj et al.'s LangChain orchestrator final_answer node:
    one round-trip, prompt is the user message, model composes the answer
    from already-summarized context.
    """
    rate_limit_error: anthropic.RateLimitError | None = None
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
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
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

        wait = 12.0 + 6.0 * attempt
        retry_after = getattr(
            getattr(rate_limit_error, "response", None), "headers", {}
        ).get("retry-after")
        if retry_after:
            try:
                wait = float(retry_after)
            except (ValueError, TypeError):
                pass
        print(
            f"  429 rate-limited; sleeping {wait:.1f}s before retry {attempt + 1}"
        )
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
