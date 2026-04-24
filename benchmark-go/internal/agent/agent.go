// Package agent runs a Claude tool-use loop against the Anthropic Messages API.
//
// Transport is a raw net/http POST to /v1/messages. This is deliberate:
//   - zero external deps (thesis-defensible; no SDK black box)
//   - byte-level control over the tools[] serialization so Python and Go
//     both send Claude the same JSON bytes for equivalent tool specs.
//
// The JSON shape mirrors benchmark/configs/config_py.py's TOOLS list exactly
// and the Python config's loop structure exactly.
package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
	"github.com/annamonso/agent-interception/benchmark-go/internal/tools"
)

// ipv4Transport forces outbound connections over tcp4 only. Works around
// machines where outbound IPv6 is broken at the network level (common on
// some home routers / ISPs). Drop-in for http.Client.Transport.
var ipv4Transport = &http.Transport{
	DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
		d := &net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}
		// Force v4 regardless of what caller asked for.
		switch network {
		case "tcp", "tcp6":
			network = "tcp4"
		}
		return d.DialContext(ctx, network, addr)
	},
	ForceAttemptHTTP2:     true,
	MaxIdleConns:          10,
	IdleConnTimeout:       90 * time.Second,
	TLSHandshakeTimeout:   10 * time.Second,
	ExpectContinueTimeout: 1 * time.Second,
}

const (
	apiEndpoint      = "https://api.anthropic.com/v1/messages"
	anthropicVersion = "2023-06-01"
	defaultModel     = "claude-haiku-4-5-20251001"
	defaultMaxTokens = 1024
	maxTurns         = 10
	requestTimeout   = 60 * time.Second
)

// defaultInterTurnPause keeps us under the 5 rpm rate limit even for
// 3-turn queries when paired with a similar pause between whole queries.
// 15s -> max 4 calls/min per query. Overridable via ANTHROPIC_INTER_TURN_PAUSE (seconds).
const defaultInterTurnPause = 15 * time.Second

func interTurnPause() time.Duration {
	raw := os.Getenv("ANTHROPIC_INTER_TURN_PAUSE")
	if raw == "" {
		return defaultInterTurnPause
	}
	var secs float64
	if _, err := fmt.Sscanf(raw, "%f", &secs); err == nil && secs >= 0 {
		return time.Duration(secs * float64(time.Second))
	}
	return defaultInterTurnPause
}

// Tool spec matching the Python config byte-for-byte.
var toolSpecs = []map[string]any{
	{
		"name":        "web_search",
		"description": "Search the web for pages relevant to a query. Returns a list of URLs (up to 10). Use this first to find sources.",
		"input_schema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"query": map[string]any{"type": "string"}},
			"required":   []string{"query"},
		},
	},
	{
		"name":        "fetch_url",
		"description": "Fetch the readable plain-text content of a URL. Use on URLs returned by web_search. Fetch at most 2-3 pages.",
		"input_schema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"url": map[string]any{"type": "string"}},
			"required":   []string{"url"},
		},
	},
	{
		"name":        "summarize",
		"description": "Run LexRank extractive summarization on a text blob, returning the n most salient sentences.",
		"input_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"text":        map[string]any{"type": "string"},
				"n_sentences": map[string]any{"type": "integer", "default": 1},
			},
			"required": []string{"text"},
		},
	},
}

const systemPrompt = "You are a web-augmented question-answering assistant. " +
	"To answer the user's question, use the provided tools: " +
	"first search the web, then fetch up to 2 URLs, then summarize each, " +
	"then produce a concise final answer citing the summaries. " +
	"Stop as soon as you have enough information."

// Query is the minimal shape we need from the queries JSON.
type Query struct {
	QueryID  string `json:"query_id"`
	Question string `json:"question"`
}

// Run executes one query through the Claude tool-use loop, emitting spans
// via the provided Observer. Returns the final answer text.
func Run(q Query, o *obs.Observer) (string, error) {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	if apiKey == "" {
		return "", fmt.Errorf("ANTHROPIC_API_KEY not set")
	}
	model := envDefault("ANTHROPIC_MODEL", defaultModel)

	root := o.Root("agent.query", map[string]any{"query_text": q.Question})
	defer root.End()

	messages := []map[string]any{
		{"role": "user", "content": q.Question},
	}

	var finalAnswer string
	client := &http.Client{Timeout: requestTimeout, Transport: ipv4Transport}
	pause := interTurnPause()

	for turn := 0; turn < maxTurns; turn++ {
		if turn > 0 && pause > 0 {
			time.Sleep(pause)
		}
		resp, turnErr := callMessages(client, apiKey, model, messages, o)
		if turnErr != nil {
			return finalAnswer, turnErr
		}

		// Append assistant content verbatim (Anthropic requires this shape for
		// the next request to resolve tool_use_id references).
		messages = append(messages, map[string]any{
			"role":    "assistant",
			"content": resp.Content,
		})

		// Extract any text the model produced this turn.
		for _, block := range resp.Content {
			if block.Type == "text" && block.Text != "" {
				finalAnswer = block.Text
			}
		}

		if resp.StopReason != "tool_use" {
			return finalAnswer, nil
		}

		// Execute each tool_use block, build tool_result blocks for the next turn.
		var results []map[string]any
		for _, block := range resp.Content {
			if block.Type != "tool_use" {
				continue
			}
			content := dispatchTool(block, q.QueryID, o)
			results = append(results, map[string]any{
				"type":          "tool_result",
				"tool_use_id":   block.ID,
				"content":       content,
			})
		}
		if len(results) == 0 {
			return finalAnswer, nil
		}
		messages = append(messages, map[string]any{"role": "user", "content": results})
	}
	return finalAnswer, fmt.Errorf("hit max_turns=%d without end_turn", maxTurns)
}

type contentBlock struct {
	Type  string          `json:"type"`
	Text  string          `json:"text,omitempty"`
	ID    string          `json:"id,omitempty"`
	Name  string          `json:"name,omitempty"`
	Input json.RawMessage `json:"input,omitempty"`
}

type messagesResponse struct {
	Content    []contentBlock `json:"content"`
	StopReason string         `json:"stop_reason"`
	Usage      struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

// callMessages makes up to maxRetries+1 attempts. Each HTTP attempt is its own
// llm.generate span, so llm.generate wall time reflects only the actual API call.
// Between attempts, a separate llm.retry_wait span measures the backoff sleep -
// honest attribution that keeps retry cost out of the LLM-time number.
func callMessages(c *http.Client, apiKey, model string, messages []map[string]any, o *obs.Observer) (*messagesResponse, error) {
	const maxRetries = 4

	payload := map[string]any{
		"model":      model,
		"max_tokens": defaultMaxTokens,
		"system":     systemPrompt,
		"tools":      toolSpecs,
		"messages":   messages,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	var lastStatus int
	var lastBody []byte

	for attempt := 0; attempt <= maxRetries; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		req, rerr := http.NewRequestWithContext(ctx, http.MethodPost, apiEndpoint, bytes.NewReader(body))
		if rerr != nil {
			cancel()
			return nil, rerr
		}
		req.Header.Set("x-api-key", apiKey)
		req.Header.Set("anthropic-version", anthropicVersion)
		req.Header.Set("content-type", "application/json")

		// One llm.generate span per HTTP attempt.
		span := o.Start("llm.generate", map[string]any{
			"llm.model":       model,
			"llm.provider":    "anthropic",
			"llm.parse_error": false,
			"llm.attempt":     attempt,
		})
		resp, derr := c.Do(req)
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
			// Terminal (success or non-retryable error). Close the span with
			// the usual attrs if we can parse the body.
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
			err := fmt.Errorf("anthropic /v1/messages status %d: %s", resp.StatusCode, truncate(string(respBody), 300))
			span.Fail(err)
			span.End()
			return nil, err
		}

		// 429. Close this attempt's span, then open a separate retry_wait span.
		span.Set("llm.rate_limited", true)
		span.End()

		if attempt == maxRetries {
			return nil, fmt.Errorf("anthropic /v1/messages: max retries exhausted; last status %d: %s",
				lastStatus, truncate(string(lastBody), 300))
		}
		wait := parseRetryAfter(resp.Header.Get("Retry-After"))
		if wait <= 0 {
			wait = time.Duration(12+6*attempt) * time.Second
		}
		waitSpan := o.Start("llm.retry_wait", map[string]any{
			"llm.retry_after_s":   wait.Seconds(),
			"llm.retry_attempt":   attempt + 1,
			"llm.retry_trigger":   "http_429",
		})
		fmt.Fprintf(os.Stderr, "  429 rate-limited; sleeping %s before retry %d\n", wait, attempt+1)
		time.Sleep(wait)
		waitSpan.End()
	}
	return nil, fmt.Errorf("unreachable")
}

func dispatchTool(block contentBlock, queryID string, o *obs.Observer) string {
	var inputs map[string]any
	if len(block.Input) > 0 {
		_ = json.Unmarshal(block.Input, &inputs)
	}
	switch block.Name {
	case "web_search":
		q, _ := inputs["query"].(string)
		urls := tools.Search(q, queryID, 10, o)
		return strings.Join(urls, "\n")
	case "fetch_url":
		u, _ := inputs["url"].(string)
		return tools.Fetch(u, o)
	case "summarize":
		txt, _ := inputs["text"].(string)
		n := 1
		if raw, ok := inputs["n_sentences"]; ok {
			switch v := raw.(type) {
			case float64:
				n = int(v)
			case int:
				n = v
			}
		}
		return tools.Summarize(txt, n, o)
	default:
		return fmt.Sprintf("unknown tool: %s", block.Name)
	}
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
