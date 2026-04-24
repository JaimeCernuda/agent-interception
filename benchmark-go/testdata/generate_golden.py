"""Generate a golden trace from the Python obs layer with a deterministic clock.

The Go obs layer's schema-equivalence test reads this file and diffs its own
output against it. Anything that changes span JSON shape in either language
should cause this test to fail.

Usage:
  uv run --group benchmark python benchmark-go/testdata/generate_golden.py

Writes: benchmark-go/testdata/golden_trace.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from benchmark.obs import Observer


# Deterministic clock: every time.monotonic_ns() / time.process_time_ns() call
# returns the next value from these lists (in order). Keeps wall_time_ms and
# cpu_time_ms stable across runs.

WALL_SEQUENCE = [
    1_000_000_000,    # root start
    1_000_001_000,    # search start
    1_050_000_000,    # search end   (wall 49ms)
    1_050_001_000,    # fetch start
    1_250_000_000,    # fetch end    (wall 199.999ms)
    1_250_001_000,    # summarize start
    1_280_000_000,    # summarize end (wall 29.999ms)
    1_280_001_000,    # llm start
    1_780_000_000,    # llm end      (wall 499.999ms)
    1_780_100_000,    # root end     (wall ~780.1ms)
]

CPU_SEQUENCE = [
    500_000_000,      # root cpu start
    500_000_100,      # search cpu start
    500_001_100,      # search cpu end  (cpu 1us)
    500_001_200,      # fetch cpu start
    500_002_200,      # fetch cpu end   (cpu 1us - network wait, barely any cpu)
    500_002_300,      # summarize cpu start
    500_022_300,      # summarize cpu end (cpu 20us)
    500_022_400,      # llm cpu start
    500_022_500,      # llm cpu end     (cpu 0.1us - network wait)
    500_022_600,      # root cpu end
]


def main() -> None:
    out_dir = Path("benchmark-go/testdata")
    out_dir.mkdir(parents=True, exist_ok=True)

    wall_iter = iter(WALL_SEQUENCE)
    cpu_iter = iter(CPU_SEQUENCE)

    with patch("benchmark.obs.time.process_time_ns", side_effect=lambda: next(cpu_iter)):
        with patch("opentelemetry.sdk.trace.time_ns", side_effect=lambda: next(wall_iter)):
            obs = Observer(config="golden", query_id="fixture_001", out_dir=str(out_dir))
            with obs.root(query_text="golden fixture"):
                with obs.span(
                    "tool.search",
                    **{"tool.name": "static", "tool.input_hash": "abc123def4567890",
                       "tool.retry_count": 0},
                ) as s:
                    s.set("tool.num_results", 2)
                    s.set("tool.output_size_bytes", 64)
                with obs.span(
                    "tool.fetch",
                    **{"tool.name": "fetch_url",
                       "tool.input_hash": "0000000000000001",
                       "tool.url": "https://example.test/a",
                       "tool.retry_count": 0},
                ) as s:
                    s.set("tool.http_status", 200)
                    s.set("tool.output_size_bytes", 4096)
                with obs.span(
                    "tool.summarize",
                    **{"tool.name": "lexrank",
                       "tool.input_hash": "0000000000000002",
                       "tool.retry_count": 0,
                       "tool.n_sentences_out": 1},
                ) as s:
                    s.set("tool.n_sentences_in", 42)
                    s.set("tool.output_size_bytes", 128)
                with obs.span(
                    "llm.generate",
                    **{"llm.model": "claude-sonnet-4-5",
                       "llm.provider": "anthropic",
                       "llm.parse_error": False},
                ) as s:
                    s.set("llm.input_tokens", 300)
                    s.set("llm.output_tokens", 50)

    produced = out_dir / "fixture_001.json"
    golden = out_dir / "golden_trace.json"
    produced.rename(golden)

    data = json.loads(golden.read_text())
    print(f"Wrote {golden}")
    print(f"  trace_id={data['trace_id']}  config={data['config']}  query_id={data['query_id']}")
    print(f"  {len(data['spans'])} spans:")
    for s in data["spans"]:
        print(
            f"    {s['name']:20s} kind={s['kind']:8s} "
            f"wall={s['wall_time_ms']:8.3f}ms cpu={s['cpu_time_ms']:8.3f}ms "
            f"parent={(s['parent_id'] or 'ROOT')[:8]}"
        )


if __name__ == "__main__":
    main()
