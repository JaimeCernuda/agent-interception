"""Ollama demo — sends chat requests through the main interceptor proxy.

Prerequisites:
  1. Main proxy running:  uv run agent-interceptor start
  2. Ollama running:      ollama serve
  3. A model pulled:      ollama pull llama3.2

Run with: uv run python scripts/ollama_demo.py [model]
  e.g.:   uv run python scripts/ollama_demo.py llama3.2:3b
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from _common import PROXY_URL, banner, start_session

OLLAMA_REAL = "http://localhost:11434"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def check_proxy(base_url: str) -> bool:
    async with httpx.AsyncClient(timeout=3) as c:
        try:
            r = await c.get(f"{PROXY_URL}/_interceptor/health")
            return r.status_code == 200
        except Exception:
            return False


async def detect_model(requested: str | None) -> str:
    if requested:
        return requested
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_REAL}/api/tags")
            models = r.json().get("models", [])
            if models:
                name = models[0]["name"]
                print(f"Auto-selected model: {name}")
                return name
    except Exception:
        pass
    return "llama3.2"


async def demo_non_streaming(client: httpx.AsyncClient, model: str, base_path: str) -> None:
    print("\n─── Non-streaming  /api/chat ────────────────────────────────────")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "In one sentence, what is a reverse proxy?"}],
        "stream": False,
    }
    r = await client.post(f"{base_path}/api/chat", json=payload)
    r.raise_for_status()
    content = r.json().get("message", {}).get("content", "").strip()
    print(f"  Response : {content}")


async def demo_streaming(client: httpx.AsyncClient, model: str, base_path: str) -> None:
    print("\n─── Streaming  /api/chat ────────────────────────────────────────")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply in exactly 3 bullet points."},
            {"role": "user", "content": "What are 3 benefits of observing LLM traffic in production?"},  # noqa: E501
        ],
        "stream": True,
    }
    print("  Response : ", end="", flush=True)
    async with client.stream("POST", f"{base_path}/api/chat", json=payload) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("message", {}).get("content", "")
            if delta:
                print(delta, end="", flush=True)
            if chunk.get("done"):
                print()


async def demo_generate(client: httpx.AsyncClient, model: str, base_path: str) -> None:
    print("\n─── Non-streaming  /api/generate ────────────────────────────────")
    payload = {"model": model, "prompt": "The capital of France is", "stream": False}
    r = await client.post(f"{base_path}/api/generate", json=payload)
    r.raise_for_status()
    print(f"  Response : {r.json().get('response', '').strip()}")


async def show_admin_summary(session_id: str) -> None:
    print("\n─── Interceptor admin summary ───────────────────────────────────")
    async with httpx.AsyncClient(base_url=PROXY_URL, timeout=10) as admin:
        r = await admin.get(
            "/_interceptor/interactions", params={"session_id": session_id, "limit": 10}
        )
        interactions = r.json()
        stats_r = await admin.get("/_interceptor/stats")
        stats = stats_r.json()

    print(f"  Total stored : {stats.get('total_interactions', '?')}  (all sessions)")
    print(f"  By provider  : {stats.get('by_provider', {})}")
    print()
    print(f"  {'Time':<10}  {'~':1}  {'Status':6}  {'Latency':>8}  {'Model':<20}  Path")
    print(f"  {'─'*10}  {'─':1}  {'─'*6}  {'─'*8}  {'─'*20}  {'─'*20}")
    for ix in interactions:
        ts = ix.get("timestamp", "")[:19].replace("T", " ")[11:]
        streaming = "~" if ix.get("is_streaming") else " "
        status = str(ix.get("status_code", "?"))
        latency = ix.get("total_latency_ms")
        lat_str = f"{latency:.0f}ms" if latency else "?"
        model = (ix.get("model") or "?")[:20]
        path = ix.get("path", "")[:30]
        print(f"  {ts:<10}  {streaming}  {status:<6}  {lat_str:>8}  {model:<20}  {path}")

    print(f"\n  Dashboard → Visualize → select: {session_id}")
    print(f"  {PROXY_URL}/_ui/")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    requested_model = sys.argv[1] if len(sys.argv) > 1 else None
    session_id = start_session("ollama-demo")
    banner("Ollama Demo — proxied through main interceptor", session_id)

    # Check proxy is up
    if not await check_proxy(PROXY_URL):
        print(f"ERROR: proxy not reachable at {PROXY_URL}")
        print("  → Start it with: uv run agent-interceptor start")
        return

    model = await detect_model(requested_model)

    # The session URL is already set in ANTHROPIC_BASE_URL by start_session(),
    # but for Ollama we build the session-prefixed path manually.
    session_path = f"/_session/{session_id}"

    print(f"Proxy   : {PROXY_URL}")
    print(f"Session : {session_id}")
    print(f"Model   : {model}")
    print(f"Ollama  : {OLLAMA_REAL}")

    async with httpx.AsyncClient(base_url=PROXY_URL, timeout=120) as client:
        try:
            await demo_non_streaming(client, model, session_path)
            await demo_streaming(client, model, session_path)
            await demo_generate(client, model, session_path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (502, 503, 404):
                print(f"\nERROR {exc.response.status_code}: could not reach Ollama.")
                print("  → Make sure Ollama is running:  ollama serve")
                print(f"  → And the model is pulled:      ollama pull {model}")
                return
            raise

    await asyncio.sleep(0.3)
    await show_admin_summary(session_id)


if __name__ == "__main__":
    asyncio.run(main())
