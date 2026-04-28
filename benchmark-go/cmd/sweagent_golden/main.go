// Command sweagent_golden emits a deterministic SWE-Agent trace through the
// Go obs layer with a fixed clock sequence matching
// benchmark-go/testdata/generate_sweagent_golden.py.
//
// Used only by tests/test_cross_lang/test_sweagent_schema.py.
package main

import (
	"flag"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// Must match WALL_SEQUENCE / CPU_SEQUENCE in generate_sweagent_golden.py.
// 4 regular spans (root + 3 tools) × 2 events each (start + end) = 8 calls
// to WallNs() and 8 calls to CPUNs(). Synthetic spans (llm.generate,
// bash_spawn, bash_work) carry explicit timestamps and don't touch the clock.
var wallSeq = []int64{
	1_000_000_000, // root start
	1_500_000_000, // bash_run start
	2_500_000_000, // bash_run end
	2_600_000_000, // read_file start
	2_700_000_000, // read_file end
	2_800_000_000, // write_file start
	2_900_000_000, // write_file end
	3_000_000_000, // root end
}

var cpuSeq = []int64{
	500_000_000,
	500_001_000,
	500_002_000,
	500_003_000,
	500_004_000,
	500_005_000,
	500_006_000,
	500_007_000,
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
	o := obs.NewObserverClock("sweagent_golden", "fixture_sweagent_001", *outDir, clk)

	root := o.Root("agent.query", map[string]any{
		"query_text":          "golden sweagent fixture",
		"agent.workspace_dir": "/golden/workspace",
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

	// tool.bash_run (regular)
	br := o.Start("tool.bash_run", map[string]any{
		"tool.name":            "bash_run",
		"tool.input_hash":      "0000000000000001",
		"bash.command_preview": "wc -l access.log",
		"bash.shell_wrapped":   false,
		"bash.timeout_seconds": 30,
	})
	o.EmitSyntheticSpanCPU(br, "tool.bash_spawn",
		1_510_000_000, 1_530_000_000,
		500_001_100, 500_001_200,
		map[string]any{
			"tool.name":          "bash_spawn",
			"bash.shell_wrapped": false,
			"bash.pid":           12345,
		})
	o.EmitSyntheticSpanCPU(br, "tool.bash_work",
		1_530_000_000, 2_490_000_000,
		500_001_200, 500_001_900,
		map[string]any{
			"tool.name":         "bash_work",
			"bash.exit_code":    0,
			"bash.timed_out":    false,
			"bash.stdout_bytes": 1024,
			"bash.stderr_bytes": 0,
		})
	br.Set("bash.exit_code", 0)
	br.Set("bash.timed_out", false)
	br.Set("bash.stdout_bytes", 1024)
	br.Set("bash.stderr_bytes", 0)
	br.Set("bash.stdout_truncated", false)
	br.Set("bash.stderr_truncated", false)
	br.End()

	// turn 1 (synthetic)
	o.EmitSyntheticSpanCPU(root, "llm.generate",
		2_510_000_000, 2_590_000_000,
		500_002_100, 500_002_500,
		map[string]any{
			"llm.model":        "claude-haiku-4-5-20251001",
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         1,
			"llm.has_tool_use": true,
			"llm.stop_reason":  "tool_use",
		})

	// tool.read_file
	rf := o.Start("tool.read_file", map[string]any{
		"tool.name":       "read_file",
		"tool.input_hash": "0000000000000002",
		"tool.path":       "access.log",
	})
	rf.Set("tool.size_bytes", 5_000_000)
	rf.Set("tool.truncated", true)
	rf.End()

	// turn 2 (synthetic)
	o.EmitSyntheticSpanCPU(root, "llm.generate",
		2_710_000_000, 2_790_000_000,
		500_004_100, 500_004_500,
		map[string]any{
			"llm.model":        "claude-haiku-4-5-20251001",
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         2,
			"llm.has_tool_use": true,
			"llm.stop_reason":  "tool_use",
		})

	// tool.write_file
	wf := o.Start("tool.write_file", map[string]any{
		"tool.name":       "write_file",
		"tool.input_hash": "0000000000000003",
		"tool.path":       "report.md",
	})
	wf.Set("tool.size_bytes", 256)
	wf.End()

	// turn 3 (synthetic)
	o.EmitSyntheticSpanCPU(root, "llm.generate",
		2_910_000_000, 2_990_000_000,
		500_006_100, 500_006_500,
		map[string]any{
			"llm.model":        "claude-haiku-4-5-20251001",
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         3,
			"llm.has_tool_use": false,
			"llm.stop_reason":  "end_turn",
		})

	root.End()
}
