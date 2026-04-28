// Command chemcrow is the Go counterpart of benchmark/configs/config_chemcrow_py.py.
//
// Usage:
//
//	go run ./cmd/chemcrow --query-id 0
//	go run ./cmd/chemcrow --queries ../benchmark/queries/chemcrow_20.json --only q011
//	go run ./cmd/chemcrow --out ../benchmark/output/chemcrow_go --limit 1
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/chemcrow"
	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

type queriesFile struct {
	Queries []chemcrow.Query `json:"queries"`
}

func main() {
	queriesPath := flag.String("queries",
		"../benchmark/queries/chemcrow_20.json",
		"path to chemcrow_20.json (relative to benchmark-go/)")
	outDir := flag.String("out",
		"../benchmark/output/chemcrow_go",
		"output directory for traces")
	configName := flag.String("config", "chemcrow_go", "config name in trace JSON")
	queryID := flag.String("query-id", "",
		"either an integer index (0..N-1) or a query_id (e.g. q011); blank = all")
	only := flag.String("only", "", "[deprecated alias for --query-id]")
	limit := flag.Int("limit", 0, "run first N queries (0 = all)")
	sleepSec := flag.Float64("sleep", 0.0, "seconds between queries")
	envFile := flag.String("env", "../benchmark/.env", "dotenv file (path relative to benchmark-go/)")
	forwardTo := flag.String("forward-to", os.Getenv("OBS_FORWARD_TO"),
		"optional URL: POST each completed trace for live analytics")
	concurrency := flag.Int("concurrency", 1,
		"max concurrent queries; bounded semaphore + WaitGroup. 1 = serial.")
	usePro := flag.Bool("use-pro-plan", false,
		"route each query through the Python config (Pro-plan tokens via "+
			"claude-agent-sdk + Claude Code CLI). Go retains concurrency control "+
			"but the agent loop and tool dispatch run inside the Python subprocess. "+
			"Default false: legacy raw-HTTP path with ANTHROPIC_API_KEY.")
	pythonExe := flag.String("python", "",
		"python interpreter to use when --use-pro-plan is set (default: $CHEMCROW_PYTHON or 'python3')")
	flag.Parse()
	if *concurrency < 1 {
		*concurrency = 1
	}

	if err := loadDotEnv(*envFile); err != nil {
		fmt.Fprintf(os.Stderr, "note: dotenv %s: %v (continuing with process env)\n", *envFile, err)
	}

	raw, err := os.ReadFile(*queriesPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot read queries file %s: %v\n", *queriesPath, err)
		os.Exit(2)
	}
	var qf queriesFile
	if err := json.Unmarshal(raw, &qf); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot parse queries file: %v\n", err)
		os.Exit(2)
	}

	queries := qf.Queries
	pickQid := *queryID
	if pickQid == "" {
		pickQid = *only
	}
	if pickQid != "" {
		picked := pickQuery(queries, pickQid)
		if picked == nil {
			fmt.Fprintf(os.Stderr, "ERROR: no query matched %q (have %d queries)\n", pickQid, len(queries))
			os.Exit(2)
		}
		queries = []chemcrow.Query{*picked}
	}
	if *limit > 0 && *limit < len(queries) {
		queries = queries[:*limit]
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create --out: %v\n", err)
		os.Exit(2)
	}

	transport := "anthropic-rest"
	if *usePro {
		transport = "claude-cli-via-python"
	}
	fmt.Printf("[run] config=%s queries=%d concurrency=%d transport=%s out=%s\n",
		*configName, len(queries), *concurrency, transport, *outDir)

	pyExe := *pythonExe
	if pyExe == "" {
		pyExe = os.Getenv("CHEMCROW_PYTHON")
	}
	if pyExe == "" {
		pyExe = "python3"
	}

	wallStart := time.Now()
	var failed int
	var failMu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, *concurrency)
	for i, q := range queries {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int, q chemcrow.Query) {
			defer wg.Done()
			defer func() { <-sem }()
			t0 := time.Now()
			var (
				answer string
				err    error
			)
			o := obs.NewObserver(*configName, q.QueryID, *outDir)
			o.ForwardTo = *forwardTo
			o.Label = q.Label
			if *usePro {
				// Pro-plan path: drive Claude CLI directly from Go, with our
				// Go MCP server hosting the three chemistry tools in-process.
				// Tools (and the per-tool Python RDKit subprocess) live in
				// THIS Go process — that property is what the experiment is
				// designed to measure, so we keep it on the Pro-plan path too.
				answer, err = chemcrow.RunWithCLI(q, o)
			} else {
				answer, err = chemcrow.Run(q, o)
			}
			dt := time.Since(t0)
			preview := strings.ReplaceAll(truncate(answer, 100), "\n", " ")
			if err != nil {
				failMu.Lock()
				failed++
				failMu.Unlock()
				fmt.Printf("  [%3d/%d] %s FAIL (%5.2fs) %v\n", i+1, len(queries), q.QueryID, dt.Seconds(), err)
			} else {
				fmt.Printf("  [%3d/%d] %s ok  (%5.2fs) answer=%q\n", i+1, len(queries), q.QueryID, dt.Seconds(), preview)
			}
		}(i, q)
		// --sleep gates SUBMISSION pacing, not concurrency. Useful at N=1 to
		// stay under rate limits; harmless at N>1 since the semaphore caps in-flight.
		if *sleepSec > 0 && i < len(queries)-1 && *concurrency == 1 {
			time.Sleep(time.Duration(*sleepSec * float64(time.Second)))
		}
	}
	wg.Wait()
	wallclockMs := float64(time.Since(wallStart).Microseconds()) / 1e3

	// Sweep harness greps for this exact prefix; do not change without updating
	// benchmark/sweep/modes/go.py.
	fmt.Printf("WALLCLOCK_MS=%.3f\n", wallclockMs)
	fmt.Printf("[run] done. %d/%d succeeded, %d failed. wallclock=%.2fs\n",
		len(queries)-failed, len(queries), failed, wallclockMs/1000)
	if failed > 0 {
		os.Exit(1)
	}
}

func pickQuery(queries []chemcrow.Query, sel string) *chemcrow.Query {
	// Try as integer index first.
	var idx int
	if _, err := fmt.Sscanf(sel, "%d", &idx); err == nil {
		if idx >= 0 && idx < len(queries) {
			return &queries[idx]
		}
	}
	for i := range queries {
		if queries[i].QueryID == sel {
			return &queries[i]
		}
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func loadDotEnv(path string) error {
	if path == "" {
		return nil
	}
	abs, err := filepath.Abs(path)
	if err == nil {
		path = abs
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		eq := strings.Index(line, "=")
		if eq < 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		val := strings.TrimSpace(line[eq+1:])
		val = strings.Trim(val, `"'`)
		if _, exists := os.LookupEnv(key); !exists {
			_ = os.Setenv(key, val)
		}
	}
	return sc.Err()
}
