// Package pipeline runs the same hardcoded LangChain-style pipeline as the
// Python config_pipeline_haiku.py:
//
//	web_search(query)              -> up to 10 URLs
//	fetch_url(urls[:2])            -> 2 page texts (sequential, skip on error)
//	lexrank_summarize(each text)   -> 1 sentence per page
//	llm.generate(prompt + sums)    -> single LLM call, no tools, no system
//
// The LLM is called exactly once per query. There is no agent loop, no
// tool-use protocol, no max_turns. Span names match the Python side
// ("tool.search", "tool.fetch", "tool.summarize", "llm.generate",
// "llm.retry_wait") so the analysis pipeline reads both languages with no
// fork.
//
// Used by cmd/concurrent_go (Experiment A) for the cross-language GIL/
// goroutine concurrency comparison. The agent-loop config in
// internal/agent/agent.go is kept as the byte-compatible counterpart to
// config_py.py and is unaffected.
package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
	"github.com/annamonso/agent-interception/benchmark-go/internal/tools"
)

const (
	apiEndpoint      = "https://api.anthropic.com/v1/messages"
	anthropicVersion = "2023-06-01"
	defaultModel     = "claude-haiku-4-5-20251001"
	defaultMaxTokens = 1024
	maxFetchURLs     = 2 // matches Raj's `if len(texts) >= 2: break`
	maxLLMRetries    = 4
	requestTimeout   = 60 * time.Second
)

const promptTemplate = "Based on these summaries, answer: %s\n\n%s"

// Same ipv4 transport as agent/agent.go to keep both runners' network
// behavior identical (some home routers/ISPs have broken outbound IPv6).
var ipv4Transport = &http.Transport{
	DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
		d := &net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}
		switch network {
		case "tcp", "tcp6":
			network = "tcp4"
		}
		return d.DialContext(ctx, network, addr)
	},
	ForceAttemptHTTP2:     true,
	MaxIdleConns:          100,
	MaxIdleConnsPerHost:   100,
	IdleConnTimeout:       90 * time.Second,
	TLSHandshakeTimeout:   10 * time.Second,
	ExpectContinueTimeout: 1 * time.Second,
}

// Query is the minimal shape we need from the queries JSON. Same as
// agent.Query, redeclared here so the pipeline package has no dependency
// on the agent loop.
type Query struct {
	QueryID  string
	Question string
}

// Run executes one query through the hardcoded pipeline, emitting spans via
// the provided Observer. Returns the final answer text. Mirrors
// benchmark/configs/config_pipeline_haiku.py:run() step-for-step.
func Run(q Query, o *obs.Observer) (string, error) {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	if apiKey == "" {
		return "", fmt.Errorf("ANTHROPIC_API_KEY not set")
	}
	model := envDefault("ANTHROPIC_MODEL", defaultModel)

	root := o.Root("agent.query", map[string]any{"query_text": q.Question})
	defer root.End()

	// 1. Web search
	urls := tools.Search(q.Question, q.QueryID, 10, o)

	// 2. Fetch up to maxFetchURLs pages, sequentially. Mirrors Python's
	// behavior: walk the URL list and take the first N that successfully
	// return text; skip empty/failed fetches silently (the failure is
	// recorded as a tool.fetch span with status=error in the Observer).
	texts := make([]string, 0, maxFetchURLs)
	for _, url := range urls {
		if len(texts) >= maxFetchURLs {
			break
		}
		txt := tools.Fetch(url, o)
		if txt != "" {
			texts = append(texts, txt)
		}
	}

	// 3. Summarize each fetched page.
	summaries := make([]string, 0, len(texts))
	for _, txt := range texts {
		s := tools.Summarize(txt, 1, o)
		if s != "" {
			summaries = append(summaries, s)
		}
	}

	// 4. Single LLM call. No tools, no system prompt - matches Python's
	// pipeline_llm_call exactly (and Raj's final_answer node).
	joined := joinWithBlanks(summaries)
	prompt := fmt.Sprintf(promptTemplate, q.Question, joined)
	resp, err := pipelineLLMCall(apiKey, model, prompt, o)
	if err != nil {
		root.Set("agent.architecture", "pipeline")
		root.Set("agent.terminated_reason", "natural")
		root.Set("agent.truncated", false)
		root.Set("agent.num_urls_fetched", len(texts))
		root.Set("agent.num_summaries", len(summaries))
		root.Set("agent.concurrent_batch_size", batchSizeFromEnv())
		return "", err
	}

	var finalAnswer string
	for _, b := range resp.Content {
		if b.Type == "text" {
			finalAnswer = b.Text
		}
	}

	root.Set("agent.architecture", "pipeline")
	root.Set("agent.terminated_reason", "natural")
	root.Set("agent.truncated", false)
	root.Set("agent.last_stop_reason", resp.StopReason)
	root.Set("agent.num_urls_fetched", len(texts))
	root.Set("agent.num_summaries", len(summaries))
	root.Set("agent.concurrent_batch_size", batchSizeFromEnv())
	return finalAnswer, nil
}

type contentBlock struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
}

type messagesResponse struct {
	Content    []contentBlock `json:"content"`
	StopReason string         `json:"stop_reason"`
	Usage      struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

// pipelineLLMCall is the Go counterpart of pipeline_llm_call in
// benchmark/configs/_pipeline_helpers.py. One llm.generate span per HTTP
// attempt; one llm.retry_wait sibling per backoff sleep. Same 12 + 6*attempt
// fallback wait (honors Retry-After when present).
func pipelineLLMCall(apiKey, model, prompt string, o *obs.Observer) (*messagesResponse, error) {
	payload := map[string]any{
		"model":      model,
		"max_tokens": defaultMaxTokens,
		"messages": []map[string]any{
			{"role": "user", "content": prompt},
		},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	client := &http.Client{Timeout: requestTimeout, Transport: ipv4Transport}
	var lastStatus int
	var lastBody []byte

	for attempt := 0; attempt <= maxLLMRetries; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		req, rerr := http.NewRequestWithContext(ctx, http.MethodPost, apiEndpoint, bytes.NewReader(body))
		if rerr != nil {
			cancel()
			return nil, rerr
		}
		req.Header.Set("x-api-key", apiKey)
		req.Header.Set("anthropic-version", anthropicVersion)
		req.Header.Set("content-type", "application/json")

		span := o.Start("llm.generate", map[string]any{
			"llm.model":       model,
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     attempt,
		})
		resp, derr := client.Do(req)
		if derr != nil {
			span.Fail(derr)
			span.End()
			cancel()
			return nil, derr
		}
		respBody, rerr := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()
		if rerr != nil {
			span.Fail(rerr)
			span.End()
			return nil, rerr
		}
		lastStatus = resp.StatusCode
		lastBody = respBody
		span.Set("llm.status_code", resp.StatusCode)

		if resp.StatusCode != 429 {
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				var parsed messagesResponse
				if jerr := json.Unmarshal(respBody, &parsed); jerr == nil {
					span.Set("llm.input_tokens", parsed.Usage.InputTokens)
					span.Set("llm.output_tokens", parsed.Usage.OutputTokens)
					span.Set("llm.stop_reason", parsed.StopReason)
					span.End()
					return &parsed, nil
				} else {
					span.Fail(jerr)
					span.End()
					return nil, jerr
				}
			}
			err := fmt.Errorf("anthropic /v1/messages status %d: %s",
				resp.StatusCode, truncate(string(respBody), 300))
			span.Fail(err)
			span.End()
			return nil, err
		}

		// 429 path
		span.Set("llm.rate_limited", true)
		span.End()

		if attempt == maxLLMRetries {
			return nil, fmt.Errorf("anthropic /v1/messages: max retries exhausted; last status %d: %s",
				lastStatus, truncate(string(lastBody), 300))
		}
		wait := parseRetryAfter(resp.Header.Get("Retry-After"))
		if wait <= 0 {
			wait = time.Duration(12+6*attempt) * time.Second
		}
		waitSpan := o.Start("llm.retry_wait", map[string]any{
			"llm.retry_after_s": wait.Seconds(),
			"llm.retry_attempt": attempt + 1,
			"llm.retry_trigger": "http_429",
		})
		fmt.Fprintf(os.Stderr, "  429 rate-limited; sleeping %s before retry %d\n", wait, attempt+1)
		time.Sleep(wait)
		waitSpan.End()
	}
	return nil, fmt.Errorf("unreachable")
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func parseRetryAfter(h string) time.Duration {
	if h == "" {
		return 0
	}
	var secs float64
	if _, err := fmt.Sscanf(h, "%f", &secs); err == nil && secs > 0 {
		return time.Duration(secs * float64(time.Second))
	}
	return 0
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func joinWithBlanks(parts []string) string {
	if len(parts) == 0 {
		return ""
	}
	out := parts[0]
	for _, p := range parts[1:] {
		out += "\n\n" + p
	}
	return out
}

func batchSizeFromEnv() int {
	raw := os.Getenv("CONCURRENT_BATCH_SIZE")
	if raw == "" {
		return 1
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 1 {
		return 1
	}
	return n
}
