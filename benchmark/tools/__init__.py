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


def __getattr__(name: str):
    # Lazy-import the chemistry tools so configs that don't need RDKit can still
    # `from benchmark.tools import web_search` without paying the import cost.
    if name in {"lookup_molecule", "smiles_to_3d", "compute_descriptors"}:
        from benchmark.tools import chemcrow

        return getattr(chemcrow, name)
    raise AttributeError(name)
