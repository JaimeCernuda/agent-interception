package chemcrow

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// Mirrors agent.go's transport: tcp4-only outbound to dodge broken IPv6.
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
	MaxIdleConns:          10,
	IdleConnTimeout:       90 * time.Second,
	TLSHandshakeTimeout:   10 * time.Second,
	ExpectContinueTimeout: 1 * time.Second,
}

const (
	apiEndpoint      = "https://api.anthropic.com/v1/messages"
	anthropicVersion = "2023-06-01"
	defaultModel     = "claude-haiku-4-5-20251001"
	defaultMaxTokens = 2048
	maxTurns         = 10
	requestTimeout   = 60 * time.Second
)

// SystemPrompt — kept identical to benchmark/configs/config_chemcrow_py.py
// SYSTEM_PROMPT byte-for-byte.
const SystemPrompt = "You are a chemistry research assistant. " +
	"To answer questions about molecules, use the tools provided to look up " +
	"molecules by name, generate 3D structures from SMILES, and compute " +
	"molecular descriptors. Use tools systematically: look up the molecule " +
	"first, then generate the 3D structure, then compute descriptors. " +
	"Report results clearly."

// Tool specs match the Python @tool descriptions byte-for-byte (no trailing
// punctuation differences). Keep these in lockstep with config_chemcrow_py.py
// or the cross-language schema test will diverge.
var toolSpecs = []map[string]any{
	{
		"name": "lookup_molecule",
		"description": "Look up a molecule by common name on PubChem and return its " +
			"canonical SMILES and molecular weight. Use this first to get a " +
			"SMILES string from a name like 'aspirin' or 'paclitaxel'.",
		"input_schema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"name": map[string]any{"type": "string"}},
			"required":   []string{"name"},
		},
	},
	{
		"name": "smiles_to_3d",
		"description": "Generate a 3D conformer for a SMILES string using RDKit's " +
			"ETKDG embed + MMFF94 optimization. Returns atom count, heavy-atom " +
			"count, and energy. Pass the SMILES returned by lookup_molecule.",
		"input_schema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"smiles": map[string]any{"type": "string"}},
			"required":   []string{"smiles"},
		},
	},
	{
		"name": "compute_descriptors",
		"description": "Compute molecular descriptors from a SMILES string: molecular " +
			"weight, logP, TPSA, heavy-atom count, and number of rotatable bonds. " +
			"Pure RDKit, no I/O. Pass the SMILES returned by lookup_molecule.",
		"input_schema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"smiles": map[string]any{"type": "string"}},
			"required":   []string{"smiles"},
		},
	},
}

// Query is the minimal shape this package needs from the queries JSON.
type Query struct {
	QueryID      string `json:"query_id"`
	Label        string `json:"label"`
	MoleculeName string `json:"molecule_name"`
	QueryText    string `json:"query_text"`
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Run drives one ChemCrow query through Claude's tool-use protocol via raw
// HTTP. Returns the final text. Side effect: emits the trace tree
// agent.query → llm.generate* + tool.* via the supplied Observer.
func Run(q Query, o *obs.Observer) (string, error) {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	if apiKey == "" {
		return "", fmt.Errorf("ANTHROPIC_API_KEY not set")
	}
	model := envDefault("ANTHROPIC_MODEL", defaultModel)

	root := o.Root("agent.query", map[string]any{
		"query_text":         q.QueryText,
		"chemcrow.molecule":  q.MoleculeName,
		"chemcrow.label":     q.Label,
	})
	defer root.End()

	messages := []map[string]any{
		{"role": "user", "content": q.QueryText},
	}

	client := &http.Client{Timeout: requestTimeout, Transport: ipv4Transport}
	var finalAnswer string
	var numToolCalls int
	var totalIn, totalOut int

	for turn := 0; turn < maxTurns; turn++ {
		resp, err := callMessages(client, apiKey, model, messages, o)
		if err != nil {
			root.Set("agent.error", err.Error())
			return finalAnswer, err
		}
		totalIn += resp.Usage.InputTokens
		totalOut += resp.Usage.OutputTokens

		// Append assistant content verbatim — required to resolve tool_use_id.
		messages = append(messages, map[string]any{
			"role":    "assistant",
			"content": resp.Content,
		})

		for _, b := range resp.Content {
			if b.Type == "text" && b.Text != "" {
				finalAnswer = b.Text
			}
		}

		if resp.StopReason != "tool_use" {
			break
		}

		var results []map[string]any
		for _, block := range resp.Content {
			if block.Type != "tool_use" {
				continue
			}
			content := dispatchTool(block, o)
			results = append(results, map[string]any{
				"type":        "tool_result",
				"tool_use_id": block.ID,
				"content":     content,
			})
			numToolCalls++
		}
		if len(results) == 0 {
			break
		}
		messages = append(messages, map[string]any{"role": "user", "content": results})
	}
	root.Set("agent.num_tool_calls", numToolCalls)
	root.Set("agent.total_input_tokens", totalIn)
	root.Set("agent.total_output_tokens", totalOut)
	root.Set("agent.is_error", false)
	root.Set("agent.truncated", false)
	return finalAnswer, nil
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

func callMessages(c *http.Client, apiKey, model string, messages []map[string]any, o *obs.Observer) (*messagesResponse, error) {
	const maxRetries = 4

	payload := map[string]any{
		"model":      model,
		"max_tokens": defaultMaxTokens,
		"system":     SystemPrompt,
		"tools":      toolSpecs,
		"messages":   messages,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

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
			err := fmt.Errorf("anthropic /v1/messages status %d: %s", resp.StatusCode, truncate(string(respBody), 300))
			span.Fail(err)
			span.End()
			return nil, err
		}

		span.Set("llm.rate_limited", true)
		span.End()

		if attempt == maxRetries {
			return nil, fmt.Errorf("anthropic /v1/messages: max retries; status %d", resp.StatusCode)
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

func dispatchTool(block contentBlock, o *obs.Observer) string {
	var input map[string]any
	if len(block.Input) > 0 {
		_ = json.Unmarshal(block.Input, &input)
	}
	switch block.Name {
	case "lookup_molecule":
		name, _ := input["name"].(string)
		out := LookupMolecule(name, o)
		return jsonString(out)
	case "smiles_to_3d":
		smi, _ := input["smiles"].(string)
		out := SmilesTo3D(smi, o)
		return jsonString(out)
	case "compute_descriptors":
		smi, _ := input["smiles"].(string)
		out := ComputeDescriptors(smi, o)
		return jsonString(out)
	default:
		return fmt.Sprintf("unknown tool: %s", block.Name)
	}
}

func jsonString(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
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

