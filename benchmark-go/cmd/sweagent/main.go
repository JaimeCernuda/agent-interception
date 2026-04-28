// Command sweagent is the Go counterpart of benchmark/configs/config_sweagent_py.py.
//
// Usage:
//
//	go run ./cmd/sweagent --query-id q01
//	go run ./cmd/sweagent --queries ../benchmark/queries/sweagent_20.json --concurrency 8
//	go run ./cmd/sweagent --out ../benchmark/output/sweagent_go --limit 1
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
	"github.com/annamonso/agent-interception/benchmark-go/internal/sweagent"
)

type queriesFile struct {
	Queries []sweagent.Query `json:"queries"`
}

func main() {
	queriesPath := flag.String("queries",
		"../benchmark/queries/sweagent_20.json",
		"path to sweagent_20.json (relative to benchmark-go/)")
	outDir := flag.String("out",
		"../benchmark/output/sweagent_go",
		"output directory for traces")
	configName := flag.String("config", "sweagent_go", "config name in trace JSON")
	queryID := flag.String("query-id", "",
		"either an integer index (0..N-1) or a query_id (e.g. q01); blank = all")
	limit := flag.Int("limit", 0, "run first N queries (0 = all)")
	sleepSec := flag.Float64("sleep", 0.0, "seconds between queries (only at concurrency=1)")
	envFile := flag.String("env", "../benchmark/.env", "dotenv file (path relative to benchmark-go/)")
	forwardTo := flag.String("forward-to", os.Getenv("OBS_FORWARD_TO"),
		"optional URL: POST each completed trace for live analytics")
	concurrency := flag.Int("concurrency", 1,
		"max concurrent queries; bounded semaphore + WaitGroup. 1 = serial.")
	workspaceRoot := flag.String("workspace-root",
		"../benchmark/queries",
		"root where workspace_dir paths from the queries file are resolved")
	workspaceCopyRoot := flag.String("workspace-copy-root",
		"",
		"if set, copy each per-query workspace into this directory before running. "+
			"Defaults to <out>/workspaces/.")
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
		queries = []sweagent.Query{*picked}
	}
	if *limit > 0 && *limit < len(queries) {
		queries = queries[:*limit]
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create --out: %v\n", err)
		os.Exit(2)
	}

	copyRoot := *workspaceCopyRoot
	if copyRoot == "" {
		copyRoot = filepath.Join(*outDir, "workspaces")
	}
	if err := os.MkdirAll(copyRoot, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create workspace-copy-root: %v\n", err)
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
		go func(i int, q sweagent.Query) {
			defer wg.Done()
			defer func() { <-sem }()

			// Per-query workspace: copy fixture into a fresh dir so the agent
			// can mutate files without dirtying the canonical fixture.
			srcWs := filepath.Join(*workspaceRoot, q.WorkspaceDir)
			runWs := filepath.Join(copyRoot, q.QueryID)
			if err := os.RemoveAll(runWs); err != nil {
				fmt.Fprintf(os.Stderr, "ERROR: rm runWs %s: %v\n", runWs, err)
			}
			if err := copyDir(srcWs, runWs); err != nil {
				failMu.Lock()
				failed++
				failMu.Unlock()
				fmt.Printf("  [%3d/%d] %s FAIL (copy workspace) %v\n", i+1, len(queries), q.QueryID, err)
				return
			}

			t0 := time.Now()
			o := obs.NewObserver(*configName, q.QueryID, *outDir)
			o.ForwardTo = *forwardTo
			o.Label = q.Label

			answer, err := sweagent.RunWithCLI(q, runWs, o)
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

func pickQuery(queries []sweagent.Query, sel string) *sweagent.Query {
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

func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, rerr := filepath.Rel(src, path)
		if rerr != nil {
			return rerr
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}
		return copyFile(path, target, info.Mode())
	})
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_RDWR|os.O_CREATE|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}
