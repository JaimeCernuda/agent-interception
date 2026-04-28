"""Generate a golden ChemCrow trace from the Python obs layer with a fixed clock.

The cross-language test (tests/test_cross_lang/test_chemcrow_schema.py) reads
this file and diffs it against the Go side's emitter
(benchmark-go/cmd/chemcrow_golden). Any drift in span name, kind, parent
topology, or attribute key set should fail that test.

Usage:
  uv run --group benchmark python benchmark-go/testdata/generate_chemcrow_golden.py

Writes: benchmark-go/testdata/chemcrow_golden_trace.json
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from benchmark.obs import Observer

# Deterministic clock sequence: each WallNs/CPUNs call returns the next value.
# Keeps wall_time_ms / cpu_time_ms stable across runs.
#
# Span open/close order in this fixture (one root + 3 LLM turns + 3 tool calls):
#   root start
#     llm.generate (turn 0) start
#     llm.generate (turn 0) end
#     tool.lookup_molecule start
#     tool.lookup_molecule end
#     llm.generate (turn 1) start
#     llm.generate (turn 1) end
#     tool.smiles_to_3d start
#     tool.smiles_to_3d end
#     llm.generate (turn 2) start
#     llm.generate (turn 2) end
#     tool.compute_descriptors start
#     tool.compute_descriptors end
#     llm.generate (turn 3) start
#     llm.generate (turn 3) end
#   root end
WALL_SEQUENCE = [
    1_000_000_000,  # root start
    1_000_001_000,  # llm0 start
    1_500_000_000,  # llm0 end (499.999 ms)
    1_500_001_000,  # tool.lookup start
    1_510_000_000,  # tool.lookup end (10 ms)
    1_510_001_000,  # llm1 start
    1_900_000_000,  # llm1 end (390 ms)
    1_900_001_000,  # tool.smiles_to_3d start
    2_900_000_000,  # tool.smiles_to_3d end (1000 ms — heavy)
    2_900_001_000,  # llm2 start
    3_300_000_000,  # llm2 end (400 ms)
    3_300_001_000,  # tool.compute_descriptors start
    3_310_000_000,  # tool.compute_descriptors end (10 ms)
    3_310_001_000,  # llm3 start
    3_700_000_000,  # llm3 end (390 ms)
    3_700_100_000,  # root end
]
CPU_SEQUENCE = [
    500_000_000,
    500_000_100,
    500_001_100,
    500_001_200,
    500_002_200,
    500_002_300,
    500_003_300,
    500_003_400,
    500_023_400,
    500_023_500,
    500_024_500,
    500_024_600,
    500_025_600,
    500_025_700,
    500_026_700,
    500_026_800,
]


def main() -> None:
    out_dir = Path("benchmark-go/testdata")
    out_dir.mkdir(parents=True, exist_ok=True)

    wall_iter = iter(WALL_SEQUENCE)
    cpu_iter = iter(CPU_SEQUENCE)

    with patch("benchmark.obs.time.process_time_ns", side_effect=lambda: next(cpu_iter)):
        with patch("opentelemetry.sdk.trace.time_ns", side_effect=lambda: next(wall_iter)):
            obs = Observer(config="chemcrow_golden", query_id="fixture_chemcrow_001", out_dir=str(out_dir))
            with obs.root(query_text="golden chemcrow fixture"):
                # turn 0: model decides to call lookup_molecule
                with obs.span(
                    "llm.generate",
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                    },
                ) as s:
                    s.set("llm.input_tokens", 100)
                    s.set("llm.output_tokens", 20)
                    s.set("llm.stop_reason", "tool_use")
                # tool 1
                with obs.span(
                    "tool.lookup_molecule",
                    **{
                        "tool.name": "lookup_molecule",
                        "tool.input_hash": "abc1234567890def",
                        "tool.molecule_name": "aspirin",
                        "tool.retry_count": 0,
                    },
                ) as s:
                    s.set("tool.cache_hit", False)
                    s.set("tool.smiles", "CC(=O)OC1=CC=CC=C1C(=O)O")
                    s.set("tool.molecular_weight", 180.16)
                    s.set("tool.http_status", 200)
                    s.set("tool.output_size_bytes", 96)
                # turn 1: model calls smiles_to_3d
                with obs.span(
                    "llm.generate",
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                    },
                ) as s:
                    s.set("llm.input_tokens", 200)
                    s.set("llm.output_tokens", 30)
                    s.set("llm.stop_reason", "tool_use")
                # tool 2
                with obs.span(
                    "tool.smiles_to_3d",
                    **{
                        "tool.name": "smiles_to_3d",
                        "tool.input_hash": "0000000000000001",
                        "tool.smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
                        "tool.retry_count": 0,
                    },
                ) as s:
                    s.set("rdkit.embed_attempts", 1)
                    s.set("rdkit.optimization_status", 0)
                    s.set("rdkit.optimization_iterations", 0)
                    s.set("tool.energy", 18.91)
                    s.set("tool.num_atoms", 21)
                    s.set("tool.heavy_atom_count", 13)
                    s.set("tool.output_size_bytes", 256)
                # turn 2: model calls compute_descriptors
                with obs.span(
                    "llm.generate",
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                    },
                ) as s:
                    s.set("llm.input_tokens", 300)
                    s.set("llm.output_tokens", 40)
                    s.set("llm.stop_reason", "tool_use")
                # tool 3
                with obs.span(
                    "tool.compute_descriptors",
                    **{
                        "tool.name": "compute_descriptors",
                        "tool.input_hash": "0000000000000002",
                        "tool.smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
                        "tool.retry_count": 0,
                    },
                ) as s:
                    s.set("tool.molecular_weight", 180.16)
                    s.set("tool.logp", 1.31)
                    s.set("tool.tpsa", 63.6)
                    s.set("tool.heavy_atom_count", 13)
                    s.set("tool.num_rotatable_bonds", 2)
                    s.set("tool.output_size_bytes", 192)
                # turn 3: final answer (no tool call)
                with obs.span(
                    "llm.generate",
                    **{
                        "llm.model": "claude-haiku-4-5-20251001",
                        "llm.provider": "anthropic",
                        "llm.parse_error": False,
                        "llm.attempt": 0,
                    },
                ) as s:
                    s.set("llm.input_tokens", 400)
                    s.set("llm.output_tokens", 200)
                    s.set("llm.stop_reason", "end_turn")

    produced = out_dir / "fixture_chemcrow_001.json"
    golden = out_dir / "chemcrow_golden_trace.json"
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
