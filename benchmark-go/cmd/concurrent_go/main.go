// Command concurrent_go is the Go counterpart of
// benchmark/configs/config_concurrent_py.py for Experiment A.
//
// Fans out N queries across goroutines and waits for completion. The
// concurrency limit is a buffered semaphore channel of capacity
// CONCURRENT_BATCH_SIZE (default 1). Each query gets its own Observer and
// writes its own JSON trace; the trace shape is byte-compatible with the
// Python side so benchmark/analysis/metrics.py reads both with no fork.
//
// The whole point of this experiment is the cross-language concurrency
// comparison: Python is GIL-bottlenecked on the LexRank summarize stage;
// Go is not. Both runners use the same pipeline shape (search -> fetch x2
// -> summarize x2 -> 1 LLM call) so any divergence in throughput at high
// batch sizes is attributable to the runtime, not the workload.
//
// Usage:
//
//	CONCURRENT_BATCH_SIZE=4 go run ./cmd/concurrent_go \
//	  --queries ../benchmark/queries/freshqa_20.json \
//	  --out ../benchmark/results/cell_concurrent_go_b4 \
//	  [--limit 4] [--only q002]
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
	"github.com/annamonso/agent-interception/benchmark-go/internal/pipeline"
	"github.com/annamonso/agent-interception/benchmark-go/internal/tools"
)

type queriesFile struct {
	Queries []queryRec `json:"queries"`
}

type queryRec struct {
	QueryID  string   `json:"query_id"`
	Question string   `json:"question"`
	URLs     []string `json:"urls"`
}

func main() {
	queriesPath := flag.String("queries", "", "path to queries JSON")
	outDir := flag.String("out", "", "output directory for traces (default: ../benchmark/results/cell_concurrent_go_b{N})")
	only := flag.String("only", "", "run only the given query_id")
	limit := flag.Int("limit", 0, "run first N queries (0 = all)")
	envFile := flag.String("env", "../benchmark/.env", "dotenv file to load")
	flag.Parse()

	if *queriesPath == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --queries is required")
		flag.Usage()
		os.Exit(2)
	}

	batchSize := batchSizeFromEnv()
	if batchSize < 1 {
		fmt.Fprintln(os.Stderr, "ERROR: CONCURRENT_BATCH_SIZE must be >= 1")
		os.Exit(2)
	}
	configLabel := fmt.Sprintf("cell_concurrent_go_b%d", batchSize)

	if *outDir == "" {
		*outDir = "../benchmark/results/" + configLabel
	}
	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create --out: %v\n", err)
		os.Exit(2)
	}

	if err := loadDotEnv(*envFile); err != nil {
		fmt.Fprintf(os.Stderr, "note: dotenv %s: %v (continuing with process env)\n", *envFile, err)
	}

	raw, err := os.ReadFile(*queriesPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot read queries file: %v\n", err)
		os.Exit(2)
	}
	var qf queriesFile
	if err := json.Unmarshal(raw, &qf); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot parse queries file: %v\n", err)
		os.Exit(2)
	}

	staticMap := make(map[string][]string, len(qf.Queries))
	for _, q := range qf.Queries {
		staticMap[q.QueryID] = q.URLs
	}
	tools.RegisterStaticURLs(staticMap)

	queries := qf.Queries
	if *only != "" {
		filtered := queries[:0]
		for _, q := range queries {
			if q.QueryID == *only {
				filtered = append(filtered, q)
			}
		}
		queries = filtered
	}
	if *limit > 0 && *limit < len(queries) {
		queries = queries[:*limit]
	}
	if len(queries) == 0 {
		fmt.Fprintf(os.Stderr, "ERROR: no queries matched --only=%q --limit=%d\n", *only, *limit)
		os.Exit(2)
	}

	model := envDefault("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
	backend := envDefault("SEARCH_BACKEND", "auto")
	fmt.Printf("[concurrent_go] label=%s  queries=%d  workers=%d  out=%s  model=%s  backend=%s\n",
		configLabel, len(queries), batchSize, *outDir, model, backend)

	// Buffered channel as a semaphore - cap = max concurrent goroutines.
	sem := make(chan struct{}, batchSize)
	var wg sync.WaitGroup
	var stdoutMu sync.Mutex
	var failed int
	var failedMu sync.Mutex

	tBatchStart := time.Now()
	for _, q := range queries {
		wg.Add(1)
		sem <- struct{}{} // acquire (blocks once batchSize goroutines are in flight)
		go func(q queryRec) {
			defer wg.Done()
			defer func() { <-sem }() // release

			o := obs.NewObserver(configLabel, q.QueryID, *outDir)
			o.Label = configLabel
			t0 := time.Now()
			ans, runErr := pipeline.Run(pipeline.Query{QueryID: q.QueryID, Question: q.Question}, o)
			dt := time.Since(t0).Seconds()
			preview := truncate(strings.ReplaceAll(ans, "\n", " "), 80)

			stdoutMu.Lock()
			if runErr != nil {
				fmt.Printf("  [%s] FAIL (%5.2fs)  %v\n", q.QueryID, dt, runErr)
			} else {
				fmt.Printf("  [%s] ok (%5.2fs)  answer=%q\n", q.QueryID, dt, preview)
			}
			stdoutMu.Unlock()

			if runErr != nil {
				failedMu.Lock()
				failed++
				failedMu.Unlock()
			}
		}(q)
	}
	wg.Wait()
	batchWallS := time.Since(tBatchStart).Seconds()

	throughput := float64(len(queries)) / batchWallS
	fmt.Printf("[concurrent_go] done. %d/%d succeeded, %d failed.\n", len(queries)-failed, len(queries), failed)
	fmt.Println()
	fmt.Printf("[summary] %s  (CONCURRENT_BATCH_SIZE=%d)\n", configLabel, batchSize)
	fmt.Println("  ----------------------------------------------------------------------")
	fmt.Printf("  queries completed         %d / %d\n", len(queries)-failed, len(queries))
	fmt.Printf("  batch wall-clock (s)      %10.2f\n", batchWallS)
	fmt.Printf("  throughput (q/s)          %10.4f\n", throughput)
	fmt.Println("  ----------------------------------------------------------------------")
	fmt.Println("  (run `python -m benchmark.analysis.metrics --traces-root benchmark/results --configs " + configLabel + "`")
	fmt.Println("   for per-stage breakdown of these traces)")
	if failed > 0 {
		os.Exit(1)
	}
}

func batchSizeFromEnv() int {
	raw := os.Getenv("CONCURRENT_BATCH_SIZE")
	if raw == "" {
		return 1
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return 1
	}
	return n
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// loadDotEnv: minimal .env parser, identical to cmd/run/main.go's loader.
// Does NOT overwrite values already in the process environment.
func loadDotEnv(path string) error {
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
