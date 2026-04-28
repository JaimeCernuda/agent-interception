"""Experiment A — Python concurrent runner (ThreadPoolExecutor).

Reuses the same hardcoded pipeline as `config_pipeline_haiku.py`
(web_search -> fetch_url x2 -> lexrank_summarize x2 -> single Haiku 4.5 call)
and dispatches N queries in parallel through ThreadPoolExecutor.

This is deliberately ThreadPool, not ProcessPool: the experiment is to
demonstrate Python's GIL bottleneck on the LexRank summarize stage. With
ProcessPool there would be no GIL contention, so the experiment would
answer a different question.

Each query produces its own JSON trace file under
benchmark/results/cell_concurrent_py_b{N}/, where N = CONCURRENT_BATCH_SIZE.
The Observer / OpenTelemetry stack is verified concurrency-safe (per-thread
contextvars; locked global span collector); each Observer instance owns its
own trace_id and output path.

A wall-clock measurement of the whole batch (start of first submit -> end of
last future) is printed at the end, plus per-cell summary stats. The wall-clock
batch time is the headline metric — divided by query count, it gives the
throughput Y-axis for figure 5 panel A.

Run:
    CONCURRENT_BATCH_SIZE=4 uv run --group benchmark python \
        -m benchmark.configs.config_concurrent_py \
        --queries benchmark/queries/freshqa_20.json [--limit 4]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from benchmark.analysis.metrics import load_trace, per_query_row
from benchmark.configs._pipeline_helpers import pipeline_llm_call
from benchmark.obs import Observer
from benchmark.tools import fetch_url, lexrank_summarize, web_search
from benchmark.tools.summarize import _ensure_nltk_punkt

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_URLS = 2
_PROMPT_TEMPLATE = "Based on these summaries, answer: {question}\n\n{summaries}"


def _pipeline(query: dict, obs: Observer, model: str) -> str:
    """Identical pipeline to config_pipeline_haiku.run(), inlined here so the
    concurrent runner is self-contained and the agentic configs cannot drift
    its behavior. Each call constructs its own Anthropic client (cheap, and
    keeps thread-local state clean)."""
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
        root_handle.set("agent.concurrent_batch_size", int(os.environ.get("CONCURRENT_BATCH_SIZE", "1")))
        return final_answer


def _run_one(
    query: dict,
    config_label: str,
    out_dir: Path,
    model: str,
) -> tuple[str, bool, float, str]:
    """Worker: run a single query, return (qid, ok, dt_seconds, preview_or_err)."""
    qid = query["query_id"]
    obs = Observer(
        config=config_label,
        query_id=qid,
        out_dir=out_dir,
        label=config_label,
    )
    t0 = time.time()
    try:
        ans = _pipeline(query, obs, model)
        dt = time.time() - t0
        preview = (ans or "").replace("\n", " ")[:80]
        return qid, True, dt, preview
    except Exception as e:
        dt = time.time() - t0
        return qid, False, dt, f"{type(e).__name__}: {e}"


def _print_summary(label: str, out_dir: Path, batch_wall_s: float, batch_size: int) -> None:
    rows = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            tf = load_trace(path)
        except Exception as e:
            print(f"[summary] WARN: could not load {path}: {e}")
            continue
        rows.append(per_query_row(tf))
    if not rows:
        print(f"[summary] no trace JSONs found under {out_dir}")
        return

    n = len(rows)
    median_active = statistics.median(r["active_latency_ms"] for r in rows)
    mean_llm_share = sum(r["llm_time_fraction"] for r in rows) / n
    mean_tool_share = sum(r["tool_time_fraction"] for r in rows) / n
    total_retry_waits = sum(r["num_retry_waits"] for r in rows)

    print()
    print(f"[summary] {label}  (CONCURRENT_BATCH_SIZE={batch_size})")
    print("  " + "-" * 70)
    print(f"  queries completed         {n} / {n}")
    print(f"  batch wall-clock (s)      {batch_wall_s:>10,.2f}")
    print(f"  throughput (q/s)          {n / batch_wall_s:>10,.4f}")
    print(f"  median active_latency_ms  {median_active:>10,.1f}")
    print(f"  mean LLM share of active  {mean_llm_share:>10.1%}")
    print(f"  mean tool share of active {mean_tool_share:>10.1%}")
    print(f"  total llm.retry_wait      {total_retry_waits:>10d}")
    print("  " + "-" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--out", required=False, type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", type=str, default="")
    parser.add_argument(
        "--cell-name",
        type=str,
        default="",
        help="Override the trace config label. Default is cell_concurrent_py_b{N}; "
             "pass e.g. cell_concurrent_py_hotpot_b{N} for the HotpotQA replay so "
             "downstream analysis can distinguish datasets at the trace level.",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).parent.parent / ".env")

    batch_size = int(os.environ.get("CONCURRENT_BATCH_SIZE", "1"))
    if batch_size < 1:
        print("ERROR: CONCURRENT_BATCH_SIZE must be >= 1", file=sys.stderr)
        return 2

    config_label = args.cell_name or f"cell_concurrent_py_b{batch_size}"
    out_dir = args.out or Path(f"benchmark/results/{config_label}")
    out_dir.mkdir(parents=True, exist_ok=True)

    queries_blob = json.loads(args.queries.read_text())
    queries = queries_blob["queries"]

    # Register static URLs (only used if SEARCH_BACKEND=static)
    from benchmark.tools import search as search_mod
    static_map = {q["query_id"]: q.get("urls") or [] for q in queries}
    search_mod.register_static_urls(static_map)

    if args.only:
        queries = [q for q in queries if q["query_id"] == args.only]
    if args.limit > 0:
        queries = queries[: args.limit]
    if not queries:
        print("ERROR: no queries matched filters", file=sys.stderr)
        return 2

    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    print(
        f"[concurrent_py] label={config_label}  queries={len(queries)}  "
        f"workers={batch_size}  out={out_dir}  model={model}  "
        f"backend={os.environ.get('SEARCH_BACKEND', 'auto')}"
    )

    # Pre-warm NLTK so the first concurrent batch doesn't race on download.
    print("[concurrent_py] pre-warming NLTK punkt tokenizer...")
    _ensure_nltk_punkt()

    failed = 0
    t_batch_start = time.time()
    with ThreadPoolExecutor(max_workers=batch_size) as ex:
        futures = {
            ex.submit(_run_one, q, config_label, out_dir, model): q["query_id"]
            for q in queries
        }
        for fut in as_completed(futures):
            qid = futures[fut]
            try:
                qid_done, ok, dt, info = fut.result()
            except Exception as e:
                failed += 1
                print(f"  [{qid}] WORKER-CRASH: {type(e).__name__}: {e}")
                continue
            if ok:
                print(f"  [{qid_done}] ok ({dt:5.2f}s)  answer={info!r}")
            else:
                failed += 1
                print(f"  [{qid_done}] FAIL ({dt:5.2f}s)  {info}")
    batch_wall_s = time.time() - t_batch_start

    print(f"[concurrent_py] done. {len(queries) - failed}/{len(queries)} succeeded, {failed} failed.")
    _print_summary(config_label, out_dir, batch_wall_s, batch_size)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
