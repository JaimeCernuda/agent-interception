// Command golden emits a trace through obs.go with a fixed, replayed clock
// sequence matching benchmark-go/testdata/generate_golden.py.
//
// Used only by the cross-language schema test. The sequences MUST stay in
// lockstep with the Python side.
package main

import (
	"flag"
	"os"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// These must match WALL_SEQUENCE / CPU_SEQUENCE in
// benchmark-go/testdata/generate_golden.py exactly, in call order.
//
// Call order per span: WallNs on start, CPUNs on start, WallNs on end, CPUNs on end.
var wallSeq = []int64{
	1_000_000_000, // root start
	1_000_001_000, // search start
	1_050_000_000, // search end
	1_050_001_000, // fetch start
	1_250_000_000, // fetch end
	1_250_001_000, // summarize start
	1_280_000_000, // summarize end
	1_280_001_000, // llm start
	1_780_000_000, // llm end
	1_780_100_000, // root end
}

var cpuSeq = []int64{
	500_000_000, // root cpu start
	500_000_100, // search cpu start
	500_001_100, // search cpu end
	500_001_200, // fetch cpu start
	500_002_200, // fetch cpu end
	500_002_300, // summarize cpu start
	500_022_300, // summarize cpu end
	500_022_400, // llm cpu start
	500_022_500, // llm cpu end
	500_022_600, // root cpu end
}

type fixedClock struct {
	wall []int64
	cpu  []int64
	wi   int
	ci   int
}

func (c *fixedClock) WallNs() int64 {
	v := c.wall[c.wi]
	c.wi++
	return v
}

func (c *fixedClock) CPUNs() int64 {
	v := c.cpu[c.ci]
	c.ci++
	return v
}

func main() {
	outDir := flag.String("out", "benchmark-go/testdata", "output directory")
	flag.Parse()

	clk := &fixedClock{wall: wallSeq, cpu: cpuSeq}
	o := obs.NewObserverClock("golden", "fixture_001", *outDir, clk)

	root := o.Root("agent.query", map[string]any{"query_text": "golden fixture"})

	{
		s := o.Start("tool.search", map[string]any{
			"tool.name":        "static",
			"tool.input_hash":  "abc123def4567890",
			"tool.retry_count": 0,
		})
		s.Set("tool.num_results", 2)
		s.Set("tool.output_size_bytes", 64)
		s.End()
	}
	{
		s := o.Start("tool.fetch", map[string]any{
			"tool.name":        "fetch_url",
			"tool.input_hash":  "0000000000000001",
			"tool.url":         "https://example.test/a",
			"tool.retry_count": 0,
		})
		s.Set("tool.http_status", 200)
		s.Set("tool.output_size_bytes", 4096)
		s.End()
	}
	{
		s := o.Start("tool.summarize", map[string]any{
			"tool.name":            "lexrank",
			"tool.input_hash":      "0000000000000002",
			"tool.retry_count":     0,
			"tool.n_sentences_out": 1,
		})
		s.Set("tool.n_sentences_in", 42)
		s.Set("tool.output_size_bytes", 128)
		s.End()
	}
	{
		s := o.Start("llm.generate", map[string]any{
			"llm.model":       "claude-sonnet-4-5",
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
		})
		s.Set("llm.input_tokens", 300)
		s.Set("llm.output_tokens", 50)
		s.End()
	}

	root.End()
	_ = os.Stdout // keep imports stable
}
