// Command run is the Go counterpart of benchmark/run.py.
//
// Usage:
//
//	go run ./cmd/run \
//	  --queries ../benchmark/queries/freshqa_20.json \
//	  --out ../benchmark/traces/go \
//	  [--only q002] [--limit 1] [--sleep 1.0]
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/agent"
	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
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
	queriesPath := flag.String("queries", "", "path to freshqa_20.json")
	outDir := flag.String("out", "", "output directory for traces")
	only := flag.String("only", "", "run only the given query_id")
	limit := flag.Int("limit", 0, "run first N queries (0 = all)")
	sleepSec := flag.Float64("sleep", 0.0, "seconds to sleep between queries")
	envFile := flag.String("env", "benchmark/.env", "dotenv file to load")
	forwardTo := flag.String("forward-to", "",
		"optional URL (e.g. http://localhost:8080/api/spans); POST each completed trace for live analytics")
	flag.Parse()

	if *queriesPath == "" || *outDir == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --queries and --out are required")
		flag.Usage()
		os.Exit(2)
	}

	if err := loadDotEnv(*envFile); err != nil {
		// Not fatal - env may already be set via export. Log once.
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

	// Register static URLs (same as benchmark.tools.search.register_static_urls).
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

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: cannot create --out: %v\n", err)
		os.Exit(2)
	}

	fmt.Printf("[run] config=go  queries=%d  out=%s\n", len(queries), *outDir)
	var failed int
	for i, q := range queries {
		o := obs.NewObserver("go", q.QueryID, *outDir)
		o.ForwardTo = *forwardTo
		t0 := time.Now()
		answer, err := agent.Run(agent.Query{QueryID: q.QueryID, Question: q.Question}, o)
		dt := time.Since(t0)
		preview := strings.ReplaceAll(truncate(answer, 100), "\n", " ")
		if err != nil {
			failed++
			fmt.Printf("  [%3d/%d] %s FAIL (%5.2fs) %v\n", i+1, len(queries), q.QueryID, dt.Seconds(), err)
		} else {
			fmt.Printf("  [%3d/%d] %s ok  (%5.2fs) answer=%q\n", i+1, len(queries), q.QueryID, dt.Seconds(), preview)
		}
		if *sleepSec > 0 && i < len(queries)-1 {
			time.Sleep(time.Duration(*sleepSec * float64(time.Second)))
		}
	}
	fmt.Printf("[run] done. %d/%d succeeded, %d failed.\n", len(queries)-failed, len(queries), failed)
	if failed > 0 {
		os.Exit(1)
	}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// loadDotEnv is a minimal .env loader: KEY=VALUE lines, # comments, unquoted values.
// Does NOT overwrite variables already present in the process environment.
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

// Ensure filepath is used (avoids unused-import noise if later edits drop one).
var _ = filepath.Clean
