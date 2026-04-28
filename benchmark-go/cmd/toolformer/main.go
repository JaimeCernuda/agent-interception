// Command toolformer is the Go counterpart of benchmark/configs/config_toolformer_py.py.
//
// Usage:
//
//	go run ./cmd/toolformer --query-id q01
//	go run ./cmd/toolformer --queries ../benchmark/queries/toolformer_20.json --concurrency 8
//	go run ./cmd/toolformer --out ../benchmark/output/toolformer_go --limit 1
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

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
	"github.com/annamonso/agent-interception/benchmark-go/internal/toolformer"
)

type queriesFile struct {
	Queries []toolformer.Query `json:"queries"`
}

func main() {
	queriesPath := flag.String("queries",
		"../benchmark/queries/toolformer_20.json",
		"path to toolformer_20.json (relative to benchmark-go/)")
	outDir := flag.String("out",
		"../benchmark/output/toolformer_go",
		"output directory for traces")
	configName := flag.String("config", "toolformer_go", "config name in trace JSON")
	queryID := flag.String("query-id", "",
		"either an integer index (0..N-1) or a query_id (e.g. q01); blank = all")
	limit := flag.Int("limit", 0, "run first N queries (0 = all)")
	sleepSec := flag.Float64("sleep", 0.0, "seconds between queries (only at concurrency=1)")
	envFile := flag.String("env", "../benchmark/.env", "dotenv file (path relative to benchmark-go/)")
	forwardTo := flag.String("forward-to", os.Getenv("OBS_FORWARD_TO"),
		"optional URL: POST each completed trace for live analytics")
	concurrency := flag.Int("concurrency", 1,
		"max concurrent queries; bounded semaphore + WaitGroup. 1 = serial.")
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
	if *queryID != "" {
		picked := pickQuery(queries, *queryID)
		if picked == nil {
			fmt.Fprintf(os.Stderr, "ERROR: no query matched %q (have %d queries)\n", *queryID, len(queries))
			os.Exit(2)
		}
		queries = []toolformer.Query{*picked}
	}
	if *limit > 0 && *limit < len(queries) {
		queries = queries[:*limit]
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create --out: %v\n", err)
		os.Exit(2)
	}

	fmt.Printf("[run] config=%s queries=%d concurrency=%d out=%s\n",
		*configName, len(queries), *concurrency, *outDir)

	wallStart := time.Now()
	var failed int
	var failMu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, *concurrency)
	for i, q := range queries {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int, q toolformer.Query) {
			defer wg.Done()
			defer func() { <-sem }()

			t0 := time.Now()
			o := obs.NewObserver(*configName, q.QueryID, *outDir)
			o.ForwardTo = *forwardTo
			o.Label = q.Category

			answer, err := toolformer.RunWithCLI(q, o)
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
		if *sleepSec > 0 && i < len(queries)-1 && *concurrency == 1 {
			time.Sleep(time.Duration(*sleepSec * float64(time.Second)))
		}
	}
	wg.Wait()
	wallclockMs := float64(time.Since(wallStart).Microseconds()) / 1e3

	fmt.Printf("WALLCLOCK_MS=%.3f\n", wallclockMs)
	fmt.Printf("[run] done. %d/%d succeeded, %d failed. wallclock=%.2fs\n",
		len(queries)-failed, len(queries), failed, wallclockMs/1000)
	if failed > 0 {
		os.Exit(1)
	}
}

func pickQuery(queries []toolformer.Query, sel string) *toolformer.Query {
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
