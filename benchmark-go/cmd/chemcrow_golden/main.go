// Command chemcrow_golden emits a deterministic ChemCrow trace through the
// Go obs layer with a fixed clock sequence matching
// benchmark-go/testdata/generate_chemcrow_golden.py.
//
// Used only by tests/test_cross_lang/test_chemcrow_schema.py.
package main

import (
	"flag"
	"os"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// Must match WALL_SEQUENCE in generate_chemcrow_golden.py exactly, in call order.
// Call order per span: WallNs on start, CPUNs on start, WallNs on end, CPUNs on end.
var wallSeq = []int64{
	1_000_000_000,
	1_000_001_000,
	1_500_000_000,
	1_500_001_000,
	1_510_000_000,
	1_510_001_000,
	1_900_000_000,
	1_900_001_000,
	2_900_000_000,
	2_900_001_000,
	3_300_000_000,
	3_300_001_000,
	3_310_000_000,
	3_310_001_000,
	3_700_000_000,
	3_700_100_000,
}

var cpuSeq = []int64{
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
}

type fixedClock struct {
	wall []int64
	cpu  []int64
	wi   int
	ci   int
}

func (c *fixedClock) WallNs() int64 { v := c.wall[c.wi]; c.wi++; return v }
func (c *fixedClock) CPUNs() int64  { v := c.cpu[c.ci]; c.ci++; return v }

func main() {
	outDir := flag.String("out", "benchmark-go/testdata", "output directory")
	flag.Parse()

	clk := &fixedClock{wall: wallSeq, cpu: cpuSeq}
	o := obs.NewObserverClock("chemcrow_golden", "fixture_chemcrow_001", *outDir, clk)

	root := o.Root("agent.query", map[string]any{"query_text": "golden chemcrow fixture"})
	{
		s := o.Start("llm.generate", map[string]any{
			"llm.model":       "claude-haiku-4-5-20251001",
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     0,
		})
		s.Set("llm.input_tokens", 100)
		s.Set("llm.output_tokens", 20)
		s.Set("llm.stop_reason", "tool_use")
		s.End()
	}
	{
		s := o.Start("tool.lookup_molecule", map[string]any{
			"tool.name":          "lookup_molecule",
			"tool.input_hash":    "abc1234567890def",
			"tool.molecule_name": "aspirin",
			"tool.retry_count":   0,
		})
		s.Set("tool.cache_hit", false)
		s.Set("tool.smiles", "CC(=O)OC1=CC=CC=C1C(=O)O")
		s.Set("tool.molecular_weight", 180.16)
		s.Set("tool.http_status", 200)
		s.Set("tool.output_size_bytes", 96)
		s.End()
	}
	{
		s := o.Start("llm.generate", map[string]any{
			"llm.model":       "claude-haiku-4-5-20251001",
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     0,
		})
		s.Set("llm.input_tokens", 200)
		s.Set("llm.output_tokens", 30)
		s.Set("llm.stop_reason", "tool_use")
		s.End()
	}
	{
		s := o.Start("tool.smiles_to_3d", map[string]any{
			"tool.name":        "smiles_to_3d",
			"tool.input_hash":  "0000000000000001",
			"tool.smiles":      "CC(=O)OC1=CC=CC=C1C(=O)O",
			"tool.retry_count": 0,
		})
		s.Set("rdkit.embed_attempts", 1)
		s.Set("rdkit.optimization_status", 0)
		s.Set("rdkit.optimization_iterations", 0)
		s.Set("tool.energy", 18.91)
		s.Set("tool.num_atoms", 21)
		s.Set("tool.heavy_atom_count", 13)
		s.Set("tool.output_size_bytes", 256)
		s.End()
	}
	{
		s := o.Start("llm.generate", map[string]any{
			"llm.model":       "claude-haiku-4-5-20251001",
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     0,
		})
		s.Set("llm.input_tokens", 300)
		s.Set("llm.output_tokens", 40)
		s.Set("llm.stop_reason", "tool_use")
		s.End()
	}
	{
		s := o.Start("tool.compute_descriptors", map[string]any{
			"tool.name":        "compute_descriptors",
			"tool.input_hash":  "0000000000000002",
			"tool.smiles":      "CC(=O)OC1=CC=CC=C1C(=O)O",
			"tool.retry_count": 0,
		})
		s.Set("tool.molecular_weight", 180.16)
		s.Set("tool.logp", 1.31)
		s.Set("tool.tpsa", 63.6)
		s.Set("tool.heavy_atom_count", 13)
		s.Set("tool.num_rotatable_bonds", 2)
		s.Set("tool.output_size_bytes", 192)
		s.End()
	}
	{
		s := o.Start("llm.generate", map[string]any{
			"llm.model":       "claude-haiku-4-5-20251001",
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     0,
		})
		s.Set("llm.input_tokens", 400)
		s.Set("llm.output_tokens", 200)
		s.Set("llm.stop_reason", "end_turn")
		s.End()
	}
	root.End()
	_ = os.Stdout
}
