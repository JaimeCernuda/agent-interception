"""Generate a golden Toolformer trace from the Python obs layer with a fixed clock.

The cross-language test (tests/test_cross_lang/test_toolformer_schema.py) reads
this file and diffs it against the Go side's emitter
(benchmark-go/cmd/toolformer_golden). Any drift in span name, kind, parent
topology, or attribute key set should fail that test.

Usage:
  uv run --group benchmark python benchmark-go/testdata/generate_toolformer_golden.py

Writes: benchmark-go/testdata/toolformer_golden_trace.json

Span tree (one query, one calculator call, two LLM turns):
    agent.query                      (root, attr agent.cpu_time_ms)
      ├── llm.generate (turn 0)      (synthetic)
      ├── tool.calculator            (regular span, success)
      └── llm.generate (turn 1)      (synthetic, end_turn)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from benchmark.obs import Observer

# Each "regular" span (root + 1 tool) uses 2 wall/cpu calls at start and 2 at
# end. 2 spans = 4 wall calls + 4 cpu calls. Synthetic spans don't touch the
# clock.
WALL_SEQUENCE = [
    1_000_000_000,  # root start
    1_500_000_000,  # calculator start
    1_500_500_000,  # calculator end (0.5 ms)
    3_000_000_000,  # root end (2000 ms total)
]
CPU_SEQUENCE = [
    500_000_000,
    500_001_000,
    500_002_000,
    500_003_000,
]


def main() -> None:
    out_dir = Path("benchmark-go/testdata")
    out_dir.mkdir(parents=True, exist_ok=True)

    wall_iter = iter(WALL_SEQUENCE)
    cpu_iter = iter(CPU_SEQUENCE)

    with patch("benchmark.obs.time.process_time_ns", side_effect=lambda: next(cpu_iter)):
        with patch("opentelemetry.sdk.trace.time_ns", side_effect=lambda: next(wall_iter)):
            obs = Observer(
                config="toolformer_golden",
                query_id="fixture_toolformer_001",
                out_dir=str(out_dir),
            )
            with obs.root(query_text="golden toolformer fixture") as root:
                root.set("agent.cpu_time_ms", 12.34)

                # turn 0: synthetic — model emits a tool_use
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=1_010_000_000,
                    end_ns=1_490_000_000,
                    cpu_start_ns=500_000_500,
                    cpu_end_ns=500_000_900,
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": 0,
                        "llm.has_tool_use": True,
                        "llm.stop_reason": "tool_use",
                    },
                )

                # tool.calculator (regular)
                with obs.span(
                    "tool.calculator",
                    **{
                        "tool.name": "calculator",
                        "tool.input_hash": "0000000000000001",
                        "expression": "96 + 88",
                    },
                ) as s:
                    s.set("result", 184.0)

                # turn 1: synthetic — final answer
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=1_510_000_000,
                    end_ns=2_990_000_000,
                    cpu_start_ns=500_002_100,
                    cpu_end_ns=500_002_500,
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": 1,
                        "llm.has_tool_use": False,
                        "llm.stop_reason": "end_turn",
                    },
                )

    produced = out_dir / "fixture_toolformer_001.json"
    golden = out_dir / "toolformer_golden_trace.json"
    produced.rename(golden)

    data = json.loads(golden.read_text())
    print(f"Wrote {golden}")
    print(f"  trace_id={data['trace_id']}  config={data['config']}  query_id={data['query_id']}")
    print(f"  {len(data['spans'])} spans:")
    for s in data["spans"]:
        print(
            f"    {s['name']:24s} kind={s['kind']:8s} "
            f"wall={s['wall_time_ms']:8.3f}ms parent={(s['parent_id'] or 'ROOT')[:8]}"
        )


if __name__ == "__main__":
    main()
