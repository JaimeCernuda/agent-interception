// Command toolformer_golden emits a deterministic Toolformer trace through
// the Go obs layer with a fixed clock sequence matching
// benchmark-go/testdata/generate_toolformer_golden.py.
//
// Used only by tests/test_cross_lang/test_toolformer_schema.py.
package main

import (
	"flag"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// Must match WALL_SEQUENCE / CPU_SEQUENCE in generate_toolformer_golden.py.
// 2 regular spans (root + 1 calculator) × 2 events each (start + end) = 4 calls
// to WallNs() and 4 calls to CPUNs(). Synthetic spans (llm.generate) carry
// explicit timestamps and don't touch the clock.
var wallSeq = []int64{
	1_000_000_000, // root start
	1_500_000_000, // calculator start
	1_500_500_000, // calculator end
	3_000_000_000, // root end
}

var cpuSeq = []int64{
	500_000_000,
	500_001_000,
	500_002_000,
	500_003_000,
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
	o := obs.NewObserverClock("toolformer_golden", "fixture_toolformer_001", *outDir, clk)

	root := o.Root("agent.query", map[string]any{
		"query_text": "golden toolformer fixture",
	})
	root.Set("agent.cpu_time_ms", 12.34)

	// turn 0 (synthetic)
	o.EmitSyntheticSpanCPU(root, "llm.generate",
		1_010_000_000, 1_490_000_000,
		500_000_500, 500_000_900,
		map[string]any{
			"llm.model":        "claude-haiku-4-5-20251001",
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         0,
			"llm.has_tool_use": true,
			"llm.stop_reason":  "tool_use",
		})

	// tool.calculator (regular)
	c := o.Start("tool.calculator", map[string]any{
		"tool.name":       "calculator",
		"tool.input_hash": "0000000000000001",
		"expression":      "96 + 88",
	})
	c.Set("result", 184.0)
	c.End()

	// turn 1 (synthetic, final)
	o.EmitSyntheticSpanCPU(root, "llm.generate",
		1_510_000_000, 2_990_000_000,
		500_002_100, 500_002_500,
		map[string]any{
			"llm.model":        "claude-haiku-4-5-20251001",
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         1,
			"llm.has_tool_use": false,
			"llm.stop_reason":  "end_turn",
		})

	root.End()
}
