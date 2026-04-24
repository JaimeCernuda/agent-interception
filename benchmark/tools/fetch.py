"""URL fetch tool.

Paper convention: fetch the top 2 URLs per query. We expose a single-URL fetch
(one span per URL) so trace analysis can account per-URL cost; orchestration code
is responsible for calling it up to N times.

Text extraction: trafilatura when possible, BeautifulSoup fallback. The paper uses
BeautifulSoup .get_text() which is noisier; trafilatura matches the spirit of
"plain text summarization input" more cleanly. This is a deliberate deviation.
"""
from __future__ import annotations

import time

import httpx
from bs4 import BeautifulSoup

from benchmark.obs import Observer, input_hash

_TIMEOUT = 10.0
_MAX_RETRIES = 2
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def fetch_url(url: str, obs: Observer) -> str:
    """Fetch one URL, return plain-text content. Emits one tool.fetch span."""
    with obs.span(
        "tool.fetch",
        **{
            "tool.name": "fetch_url",
            "tool.input_hash": input_hash(url),
            "tool.url": url,
            "tool.retry_count": 0,
        },
    ) as span:
        text, retries, status = _fetch_with_retries(url)
        span.set("tool.retry_count", retries)
        span.set("tool.http_status", status)
        span.set("tool.output_size_bytes", len(text.encode("utf-8")))
        return text


def _fetch_with_retries(url: str) -> tuple[str, int, int]:
    retries = 0
    last_status = 0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers=_HEADERS,
            ) as client:
                r = client.get(url)
                last_status = r.status_code
                r.raise_for_status()
                text = _extract_text(r.text)
                return text, retries, last_status
        except Exception:
            retries += 1
            if attempt == _MAX_RETRIES:
                return "", retries, last_status
            time.sleep(0.5 * (attempt + 1))
    return "", retries, last_status


def _extract_text(html: str) -> str:
    try:
        import trafilatura

        out = trafilatura.extract(html, include_comments=False, include_tables=False)
        if out:
            return out
    except Exception:
        pass
    return BeautifulSoup(html, "html.parser").get_text(separator="\n")
