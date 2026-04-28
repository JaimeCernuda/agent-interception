"""Generate a golden SWE-Agent trace from the Python obs layer with a fixed clock.

The cross-language test (tests/test_cross_lang/test_sweagent_schema.py) reads
this file and diffs it against the Go side's emitter
(benchmark-go/cmd/sweagent_golden). Any drift in span name, kind, parent
topology, or attribute key set should fail that test.

Usage:
  uv run --group benchmark python benchmark-go/testdata/generate_sweagent_golden.py

Writes: benchmark-go/testdata/sweagent_golden_trace.json

Span tree (one query, three tool calls, four LLM turns):
    agent.query                      (root, attr agent.cpu_time_ms)
      ├── llm.generate (turn 0)      (synthetic)
      ├── tool.bash_run              (regular span)
      │     ├── tool.bash_spawn      (synthetic)
      │     └── tool.bash_work       (synthetic)
      ├── llm.generate (turn 1)      (synthetic)
      ├── tool.read_file             (regular span)
      ├── llm.generate (turn 2)      (synthetic)
      ├── tool.write_file            (regular span)
      └── llm.generate (turn 3)      (synthetic)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from benchmark.obs import Observer

# Each "regular" span (root + 3 tools) uses 2 wall/cpu calls at start and 2 at
# end. 4 spans = 16 wall calls + 16 cpu calls. Synthetic spans don't touch
# the clock.
WALL_SEQUENCE = [
    1_000_000_000,  # root start
    1_500_000_000,  # bash_run start
    2_500_000_000,  # bash_run end (1000 ms)
    2_600_000_000,  # read_file start
    2_700_000_000,  # read_file end (100 ms)
    2_800_000_000,  # write_file start
    2_900_000_000,  # write_file end (100 ms)
    3_000_000_000,  # root end (2000 ms total)
]
CPU_SEQUENCE = [
    500_000_000,
    500_001_000,
    500_002_000,
    500_003_000,
    500_004_000,
    500_005_000,
    500_006_000,
    500_007_000,
]


def main() -> None:
    out_dir = Path("benchmark-go/testdata")
    out_dir.mkdir(parents=True, exist_ok=True)

    wall_iter = iter(WALL_SEQUENCE)
    cpu_iter = iter(CPU_SEQUENCE)

    with patch("benchmark.obs.time.process_time_ns", side_effect=lambda: next(cpu_iter)):
        with patch("opentelemetry.sdk.trace.time_ns", side_effect=lambda: next(wall_iter)):
            obs = Observer(
                config="sweagent_golden",
                query_id="fixture_sweagent_001",
                out_dir=str(out_dir),
            )
            with obs.root(query_text="golden sweagent fixture") as root:
                root.set("agent.workspace_dir", "/golden/workspace")
                root.set("agent.cpu_time_ms", 12.34)
                # turn 0: model decides to call bash_run (synthetic, after root start, before bash)
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
                # tool.bash_run + spawn/work children
                with obs.span(
                    "tool.bash_run",
                    **{
                        "tool.name": "bash_run",
                        "tool.input_hash": "0000000000000001",
                        "bash.command_preview": "wc -l access.log",
                        "bash.shell_wrapped": False,
                        "bash.timeout_seconds": 30,
                    },
                ) as s:
                    obs.emit_synthetic_span(
                        "tool.bash_spawn",
                        start_ns=1_510_000_000,
                        end_ns=1_530_000_000,
                        cpu_start_ns=500_001_100,
                        cpu_end_ns=500_001_200,
                        **{
                            "tool.name": "bash_spawn",
                            "bash.shell_wrapped": False,
                            "bash.pid": 12345,
                        },
                    )
                    obs.emit_synthetic_span(
                        "tool.bash_work",
                        start_ns=1_530_000_000,
                        end_ns=2_490_000_000,
                        cpu_start_ns=500_001_200,
                        cpu_end_ns=500_001_900,
                        **{
                            "tool.name": "bash_work",
                            "bash.exit_code": 0,
                            "bash.timed_out": False,
                            "bash.stdout_bytes": 1024,
                            "bash.stderr_bytes": 0,
                        },
                    )
                    s.set("bash.exit_code", 0)
                    s.set("bash.timed_out", False)
                    s.set("bash.stdout_bytes", 1024)
                    s.set("bash.stderr_bytes", 0)
                    s.set("bash.stdout_truncated", False)
                    s.set("bash.stderr_truncated", False)

                # turn 1: synthetic
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=2_510_000_000,
                    end_ns=2_590_000_000,
                    cpu_start_ns=500_002_100,
                    cpu_end_ns=500_002_500,
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": 1,
                        "llm.has_tool_use": True,
                        "llm.stop_reason": "tool_use",
                    },
                )
                # tool.read_file
                with obs.span(
                    "tool.read_file",
                    **{
                        "tool.name": "read_file",
                        "tool.input_hash": "0000000000000002",
                        "tool.path": "access.log",
                    },
                ) as s:
                    s.set("tool.size_bytes", 5_000_000)
                    s.set("tool.truncated", True)

                # turn 2
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=2_710_000_000,
                    end_ns=2_790_000_000,
                    cpu_start_ns=500_004_100,
                    cpu_end_ns=500_004_500,
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": 2,
                        "llm.has_tool_use": True,
                        "llm.stop_reason": "tool_use",
                    },
                )
                # tool.write_file
                with obs.span(
                    "tool.write_file",
                    **{
                        "tool.name": "write_file",
                        "tool.input_hash": "0000000000000003",
                        "tool.path": "report.md",
                    },
                ) as s:
                    s.set("tool.size_bytes", 256)

                # turn 3: final answer (no tool use)
                obs.emit_synthetic_span(
                    "llm.generate",
                    start_ns=2_910_000_000,
                    end_ns=2_990_000_000,
                    cpu_start_ns=500_006_100,
                    cpu_end_ns=500_006_500,
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                        "llm.turn": 3,
                        "llm.has_tool_use": False,
                        "llm.stop_reason": "end_turn",
                    },
                )

    produced = out_dir / "fixture_sweagent_001.json"
    golden = out_dir / "sweagent_golden_trace.json"
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
