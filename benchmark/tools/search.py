"""Web search tool.

Three backends, picked by SEARCH_BACKEND env var (default: 'auto'):
  - 'google_cse' : Google Custom Search. Requires GOOGLE_API_KEY + GOOGLE_CX. Matches paper.
  - 'ddg'        : DuckDuckGo via the ddgs package. No key, flaky rate-limit.
  - 'static'     : Use the pre-resolved urls[] list attached to each query in
                   queries/freshqa_20.json. Deterministic. The paper does this too
                   (see external/cpu-centric-agentic-ai/langchain/orchestrator.py
                   under --skip-web-search).
  - 'auto'       : Try Google CSE if keys set, else DDG, else static.

For thesis defensibility, prefer 'static' when measuring latency shape - it removes
network variance as a confound. Use 'google_cse' or 'ddg' when you specifically want
to measure live search cost.
"""
from __future__ import annotations

import os
import time

import httpx

from benchmark.obs import Observer, input_hash

_GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_TOP_K = 10

# Populated by the caller (run.py) before any search runs, when backend='static'.
_STATIC_URLS_BY_QUERY: dict[str, list[str]] = {}


def register_static_urls(mapping: dict[str, list[str]]) -> None:
    """run.py calls this with {query_id: [urls]} when loading the queries file."""
    _STATIC_URLS_BY_QUERY.clear()
    _STATIC_URLS_BY_QUERY.update(mapping)


def web_search(
    query: str,
    obs: Observer,
    top_k: int = _TOP_K,
    query_id: str | None = None,
) -> list[str]:
    """Return up to top_k URLs for the query. Emits one tool.search span."""
    backend = _resolve_backend()
    with obs.span(
        "tool.search",
        **{
            "tool.name": backend,
            "tool.input_hash": input_hash(query),
            "tool.retry_count": 0,
        },
    ) as span:
        urls, retries = _dispatch(backend, query, top_k, query_id)
        span.set("tool.retry_count", retries)
        span.set("tool.num_results", len(urls))
        span.set("tool.output_size_bytes", sum(len(u) for u in urls))
        return urls


def _resolve_backend() -> str:
    explicit = os.getenv("SEARCH_BACKEND", "auto").lower()
    if explicit in {"google_cse", "ddg", "static"}:
        return explicit
    # auto
    if os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CX"):
        return "google_cse"
    return "static"  # DDG is unreliable enough that we prefer deterministic default


def _dispatch(
    backend: str, query: str, top_k: int, query_id: str | None
) -> tuple[list[str], int]:
    if backend == "google_cse":
        return _google_cse(query, top_k)
    if backend == "ddg":
        return _ddg(query, top_k)
    return _static(query_id, top_k)


def _google_cse(query: str, top_k: int) -> tuple[list[str], int]:
    params = {
        "key": os.environ["GOOGLE_API_KEY"],
        "cx": os.environ["GOOGLE_CX"],
        "q": query,
        "num": min(top_k, 10),
    }
    retries = 0
    for attempt in range(3):
        try:
            r = httpx.get(_GOOGLE_ENDPOINT, params=params, timeout=10.0)
            r.raise_for_status()
            items = r.json().get("items", [])
            return [i["link"] for i in items if "link" in i][:top_k], retries
        except Exception:
            retries += 1
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    return [], retries


def _ddg(query: str, top_k: int) -> tuple[list[str], int]:
    from ddgs import DDGS

    retries = 0
    for attempt in range(3):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=top_k))
            urls = [r.get("href") or r.get("url") for r in results]
            urls = [u for u in urls if u][:top_k]
            if urls:
                return urls, retries
        except Exception:
            pass
        retries += 1
        time.sleep(1.0 * (attempt + 1))
    return [], retries


def _static(query_id: str | None, top_k: int) -> tuple[list[str], int]:
    if query_id is None:
        raise RuntimeError(
            "SEARCH_BACKEND=static requires query_id to be passed to web_search(...)"
        )
    urls = _STATIC_URLS_BY_QUERY.get(query_id)
    if not urls:
        raise RuntimeError(
            f"SEARCH_BACKEND=static but no urls registered for query_id={query_id}. "
            "Check queries/freshqa_20.json has a 'urls' field for this query, "
            "and that run.py called register_static_urls()."
        )
    return list(urls)[:top_k], 0
