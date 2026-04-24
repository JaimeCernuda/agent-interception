"""Shared, instrumented tools used by all three configs.

Each tool function takes an `obs: Observer` and emits a single `tool.*` span
that records wall time, cpu time, retry_count, input hash, and output size.

These are deliberately plain functions, not LangChain Tool objects. Configs A and B
call them directly. Config C calls them through an Anthropic tool-use dispatch table.
"""
from benchmark.tools.fetch import fetch_url
from benchmark.tools.search import web_search
from benchmark.tools.summarize import lexrank_summarize

__all__ = ["web_search", "fetch_url", "lexrank_summarize"]
