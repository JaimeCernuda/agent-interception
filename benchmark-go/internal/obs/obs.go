// Package obs is the Go equivalent of benchmark/obs.py.
//
// Emits JSON traces byte-compatible with the Python side's output:
// same field names, same field types, same parent-child tree shape,
// same kind ("root" | "tool" | "llm" | "internal") semantics.
//
// Schema contract lives in benchmark-go/testdata/golden_trace.json.
// Any shape drift here should be caught by tests/test_cross_lang/.
//
// Kept deliberately small (~200 LoC) so it can be read in one sitting.
// If it grows, push helpers out instead of stacking features here.
package obs

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Clock abstracts time so tests can feed a deterministic sequence.
type Clock interface {
	WallNs() int64
	CPUNs() int64
}

type realClock struct{}

func (realClock) WallNs() int64 { return time.Now().UnixNano() }
func (realClock) CPUNs() int64 {
	// Go has no public process-CPU clock that matches Python's
	// time.process_time_ns() exactly. We use monotonic - wall jitter is
	// small; the Python side measures CPU at Python granularity (nanosec
	// but coarse on macOS). Documented honestly in the report.
	return monotonicNs()
}

// Observer is one trace per query, same contract as benchmark/obs.py.
//
// If ForwardTo is non-empty, the finished trace JSON is POSTed there when the
// root span closes (in addition to being written to disk). Forward failures are
// logged to stderr but never abort the run.
type Observer struct {
	Config    string
	QueryID   string
	OutDir    string
	ForwardTo string // optional: e.g. "http://localhost:8080/api/spans"
	Label     string // optional human-readable label

	clock  Clock
	mu     sync.Mutex
	stack  []*spanInternal // active-span stack; top is current
	done   []*spanInternal // finished spans, preserved insertion order
	traceID string
}

// Span is the caller-facing handle returned by Observer.Start*.
// Callers: h.Set("tool.url", u); h.End() (or use helper With()).
type Span struct {
	obs *Observer
	sp  *spanInternal
}

type spanInternal struct {
	Name     string
	TraceID  string
	SpanID   string
	ParentID string // "" means root
	StartNs  int64
	EndNs    int64
	CPUStart int64
	CPUEnd   int64
	Attrs    map[string]any
	Kind     string
	Status   string
	Error    string
}

// NewObserver constructs with real clocks. Tests use NewObserverClock.
func NewObserver(config, queryID, outDir string) *Observer {
	return NewObserverClock(config, queryID, outDir, realClock{})
}

func NewObserverClock(config, queryID, outDir string, clk Clock) *Observer {
	_ = os.MkdirAll(outDir, 0o755)
	return &Observer{Config: config, QueryID: queryID, OutDir: outDir, clock: clk}
}

// Root opens the top-level agent.query span. Caller MUST call End() on the
// returned Span; on End the full trace is flushed to <OutDir>/<QueryID>.json.
func (o *Observer) Root(name string, attrs map[string]any) *Span {
	merged := map[string]any{
		"config":    o.Config,
		"query_id":  o.QueryID,
	}
	for k, v := range attrs {
		merged[k] = v
	}
	return o.start(name, kindFor(name, true), merged)
}

// Start opens a child span under the current top-of-stack span.
func (o *Observer) Start(name string, attrs map[string]any) *Span {
	return o.start(name, kindFor(name, false), attrs)
}

func (o *Observer) start(name, kind string, attrs map[string]any) *Span {
	o.mu.Lock()
	defer o.mu.Unlock()

	var parentID string
	if len(o.stack) > 0 {
		parentID = o.stack[len(o.stack)-1].SpanID
	}

	traceID := o.traceID
	if traceID == "" {
		traceID = randHex(16)
		o.traceID = traceID
	}

	sp := &spanInternal{
		Name:     name,
		TraceID:  traceID,
		SpanID:   randHex(8),
		ParentID: parentID,
		StartNs:  o.clock.WallNs(),
		CPUStart: o.clock.CPUNs(),
		Attrs:    map[string]any{},
		Kind:     kind,
		Status:   "ok",
	}
	for k, v := range attrs {
		sp.Attrs[k] = v
	}
	o.stack = append(o.stack, sp)
	return &Span{obs: o, sp: sp}
}

// Set attaches an attribute to the span. Overwrites on duplicate key.
func (s *Span) Set(key string, value any) {
	s.obs.mu.Lock()
	defer s.obs.mu.Unlock()
	s.sp.Attrs[key] = value
}

// Fail marks the span as an error with a message; End is still required.
func (s *Span) Fail(err error) {
	s.obs.mu.Lock()
	defer s.obs.mu.Unlock()
	s.sp.Status = "error"
	if err != nil {
		s.sp.Error = err.Error()
	}
}

// End closes the span. If the span is the root, the full trace is flushed.
func (s *Span) End() {
	s.obs.mu.Lock()
	defer s.obs.mu.Unlock()

	s.sp.EndNs = s.obs.clock.WallNs()
	s.sp.CPUEnd = s.obs.clock.CPUNs()

	// Pop stack until we remove this span. (Handles unbalanced End() gracefully
	// by still closing the span in-place.)
	for i := len(s.obs.stack) - 1; i >= 0; i-- {
		if s.obs.stack[i] == s.sp {
			s.obs.stack = append(s.obs.stack[:i], s.obs.stack[i+1:]...)
			break
		}
	}
	s.obs.done = append(s.obs.done, s.sp)

	if s.sp.ParentID == "" {
		s.obs.flush()
	}
}

func (o *Observer) flush() {
	// Sorted by start_ns to match Python's exporter.
	spans := append([]*spanInternal(nil), o.done...)
	sort.SliceStable(spans, func(i, j int) bool { return spans[i].StartNs < spans[j].StartNs })

	records := make([]map[string]any, 0, len(spans))
	for _, sp := range spans {
		records = append(records, spanToDict(sp))
	}

	out := map[string]any{
		"trace_id":  o.traceID,
		"config":    o.Config,
		"query_id":  o.QueryID,
		"label":     o.Label,
		"spans":     records,
	}
	path := filepath.Join(o.OutDir, o.QueryID+".json")
	f, err := os.Create(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "obs: cannot write %s: %v\n", path, err)
		return
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	_ = enc.Encode(out)

	if o.ForwardTo != "" {
		forwardTrace(o.ForwardTo, out)
	}
}

// forwardTrace POSTs a finished trace to the analytics ingest endpoint.
// Best-effort; failures are logged but never abort the run.
func forwardTrace(url string, payload map[string]any) {
	body, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintf(os.Stderr, "obs: forward marshal: %v\n", err)
		return
	}
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		fmt.Fprintf(os.Stderr, "obs: forward build request: %v\n", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		fmt.Fprintf(os.Stderr, "obs: forward to %s failed: %v\n", url, err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		fmt.Fprintf(os.Stderr, "obs: forward to %s got status %d\n", url, resp.StatusCode)
	}
}

func spanToDict(sp *spanInternal) map[string]any {
	wall := float64(sp.EndNs-sp.StartNs) / 1e6
	cpu := float64(sp.CPUEnd-sp.CPUStart) / 1e6

	var parent any
	if sp.ParentID == "" {
		parent = nil
	} else {
		parent = sp.ParentID
	}
	var errField any
	if sp.Error != "" {
		errField = sp.Error
	} else {
		errField = nil
	}

	return map[string]any{
		"name":          sp.Name,
		"trace_id":      sp.TraceID,
		"span_id":       sp.SpanID,
		"parent_id":     parent,
		"start_ns":      sp.StartNs,
		"end_ns":        sp.EndNs,
		"wall_time_ms":  wall,
		"cpu_time_ms":   cpu,
		"kind":          sp.Kind,
		"attrs":         sp.Attrs,
		"status":        sp.Status,
		"error":         errField,
	}
}

func kindFor(name string, root bool) string {
	if root {
		return "root"
	}
	if strings.HasPrefix(name, "llm.") {
		return "llm"
	}
	if strings.HasPrefix(name, "tool.") {
		return "tool"
	}
	return "internal"
}

func randHex(nBytes int) string {
	b := make([]byte, nBytes)
	if _, err := rand.Read(b); err != nil {
		// Non-crypto fallback with timestamp - never expected in practice.
		ts := time.Now().UnixNano()
		for i := range b {
			b[i] = byte(ts >> (8 * uint(i%8)))
		}
	}
	return hex.EncodeToString(b)
}

// monotonicNs returns a monotonic-ish timestamp in ns. time.Now().UnixNano()
// is sufficient for our durations (ms granularity matters, ns does not).
func monotonicNs() int64 { return time.Now().UnixNano() }
