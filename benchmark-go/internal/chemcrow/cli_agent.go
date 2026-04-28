package chemcrow

// RunWithCLI drives one ChemCrow query via the Claude CLI subprocess (Pro-plan
// tokens), with the in-process MCP HTTP server (mcp_server.go) hosting the
// three Go tools. Mirrors benchmark/configs/config_chemcrow_py.py's _run_async
// timing logic: one llm.generate span per real API turn, aggregated from the
// CLI's chunked AssistantMessage stream.
//
// Why not the SDK: there is no Go claude-agent-sdk. The Python SDK shells out
// to the same Claude CLI binary we drive here. Reading stream-json directly
// keeps Go free of any non-stdlib dependency.

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const (
	cliBin            = "claude"
	cliMaxTurns       = 10
	cliRequestTimeout = 10 * time.Minute
	cliModelDefault   = "claude-haiku-4-5"
)

// claude CLI events we care about. Other types (rate_limit_event, system,
// stream_event) we just ignore.
type cliEvent struct {
	Type    string          `json:"type"`
	Message json.RawMessage `json:"message,omitempty"`
	Subtype string          `json:"subtype,omitempty"`
	UUID    string          `json:"uuid,omitempty"`
	// Only on type == "user" tool-result wrappers
	ToolUseResult json.RawMessage `json:"tool_use_result,omitempty"`
	// Only on type == "result"
	IsError       bool    `json:"is_error,omitempty"`
	NumTurns      int     `json:"num_turns,omitempty"`
	DurationMS    float64 `json:"duration_ms,omitempty"`
	DurationAPIMS float64 `json:"duration_api_ms,omitempty"`
	TotalCostUSD  float64 `json:"total_cost_usd,omitempty"`
	Result        string  `json:"result,omitempty"`
	Usage         struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage,omitempty"`
}

type cliAssistantMessage struct {
	Model   string                 `json:"model"`
	Content []cliAssistantContent  `json:"content"`
	Usage   map[string]json.Number `json:"usage,omitempty"`
}

type cliAssistantContent struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	Thinking string `json:"thinking,omitempty"`
	Name     string `json:"name,omitempty"`
}

const cliSystemPrompt = SystemPrompt

func model() string {
	if v := os.Getenv("ANTHROPIC_MODEL"); v != "" {
		return v
	}
	return cliModelDefault
}

// RunWithCLI is the entry point for Pro-plan Go runs. Returns the final
// assistant text. Side-effect: emits the agent.query trace via `o`, with
// llm.generate + tool.* children.
func RunWithCLI(q Query, o *obs.Observer) (string, error) {
	if _, err := exec.LookPath(cliBin); err != nil {
		return "", fmt.Errorf("claude CLI not found in PATH: %w", err)
	}
	_, mcpURL, cleanup, err := registerObserver(o)
	if err != nil {
		return "", fmt.Errorf("mcp host: %w", err)
	}
	defer cleanup()

	mcpConfig := map[string]any{
		"mcpServers": map[string]any{
			"chemcrow": map[string]any{
				"type": "http",
				"url":  mcpURL,
			},
		},
	}
	mcpJSON, err := json.Marshal(mcpConfig)
	if err != nil {
		return "", fmt.Errorf("mcp config: %w", err)
	}

	args := []string{
		"--print",
		"--output-format", "stream-json",
		"--input-format", "stream-json",
		"--verbose",
		"--max-turns", fmt.Sprintf("%d", cliMaxTurns),
		"--model", model(),
		"--system-prompt", cliSystemPrompt,
		"--mcp-config", string(mcpJSON),
		"--allowed-tools",
		"mcp__chemcrow__lookup_molecule," +
			"mcp__chemcrow__smiles_to_3d," +
			"mcp__chemcrow__compute_descriptors",
		"--permission-mode", "bypassPermissions",
	}

	ctx, cancel := context.WithTimeout(context.Background(), cliRequestTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, cliBin, args...)
	// Strip ANTHROPIC_API_KEY from the CLI's environment. With it set, the
	// CLI prefers the metered API path; for our use case we want Pro-plan
	// auth (apiKeySource: "none"). The metered key is also unfunded in this
	// repo's .env, so leaving it set produces "Credit balance is too low"
	// errors instead of falling back to OAuth/Pro.
	env := os.Environ()
	scrubbed := env[:0]
	for _, kv := range env {
		if strings.HasPrefix(kv, "ANTHROPIC_API_KEY=") {
			continue
		}
		scrubbed = append(scrubbed, kv)
	}
	cmd.Env = scrubbed
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return "", err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return "", err
	}
	var stderrBuf bytes.Buffer
	cmd.Stderr = &stderrBuf

	root := o.Root("agent.query", map[string]any{
		"query_text":        q.QueryText,
		"chemcrow.molecule": q.MoleculeName,
		"chemcrow.label":    q.Label,
	})
	defer root.End()

	if err := cmd.Start(); err != nil {
		root.Set("agent.error", err.Error())
		root.Fail(err)
		return "", err
	}

	// Feed the user message, then close stdin so the CLI knows we're done.
	userMsg := map[string]any{
		"type": "user",
		"message": map[string]any{
			"role":    "user",
			"content": q.QueryText,
		},
	}
	if err := json.NewEncoder(stdin).Encode(userMsg); err != nil {
		_ = cmd.Process.Kill()
		root.Set("agent.error", "stdin write: "+err.Error())
		root.Fail(err)
		return "", err
	}
	_ = stdin.Close()

	finalText, agentErr := consumeStream(stdout, o, root)

	waitErr := cmd.Wait()

	if agentErr != nil {
		root.Set("agent.error", agentErr.Error())
		root.Fail(agentErr)
		_ = stderrBuf.String()
		return finalText, agentErr
	}
	if waitErr != nil {
		// Treat non-zero CLI exit as an error only if no result message
		// arrived (consumeStream already verified at least one).
		root.Set("agent.cli_exit_error", waitErr.Error())
	}
	return finalText, nil
}

// consumeStream reads cliEvents from the CLI's stdout, aggregates assistant
// chunks into per-turn llm.generate spans, observes user/tool-result
// boundaries, and stops on result.
//
// Span emission timing exactly mirrors config_chemcrow_py.py:
//   - boundary moves to NOW after each tool result
//   - llm.generate span [boundary, last_assistant_event_time] closes when a
//     UserMessage tool_use_result is observed, or when ResultMessage arrives
//     (final turn).
func consumeStream(stdout io.Reader, o *obs.Observer, root *obs.Span) (string, error) {
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 1<<20), 8<<20) // up to 8 MB lines

	var (
		boundary       = time.Now().UnixNano()
		turnActive     bool
		turnLastNS     int64
		turnHasToolUse bool
		turnModel      = model()
		turnIndex      int
		finalChunks    []string
		sawResult      bool
		emitErr        error
	)

	closeTurn := func() {
		if !turnActive {
			return
		}
		stopReason := "end_turn"
		if turnHasToolUse {
			stopReason = "tool_use"
		}
		o.EmitSyntheticSpan(root, "llm.generate", boundary, turnLastNS, map[string]any{
			"llm.model":        turnModel,
			"llm.provider":     "anthropic",
			"llm.parse_error":  false,
			"llm.attempt":      0,
			"llm.turn":         turnIndex,
			"llm.has_tool_use": turnHasToolUse,
			"llm.stop_reason":  stopReason,
		})
		turnIndex++
		turnActive = false
		turnHasToolUse = false
		boundary = turnLastNS
	}

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var ev cliEvent
		if err := json.Unmarshal(line, &ev); err != nil {
			// CLI also occasionally emits non-JSON debug lines; skip them.
			continue
		}
		switch ev.Type {
		case "system":
			// init / status — nothing to attribute.
		case "rate_limit_event":
			// noted but not emitted as a span.
		case "stream_event":
			// micro-events used for incremental UI; ignore.
		case "assistant":
			now := time.Now().UnixNano()
			turnActive = true
			turnLastNS = now
			var msg cliAssistantMessage
			if err := json.Unmarshal(ev.Message, &msg); err == nil {
				if msg.Model != "" {
					turnModel = msg.Model
				}
				for _, c := range msg.Content {
					switch c.Type {
					case "text":
						if c.Text != "" {
							finalChunks = append(finalChunks, c.Text)
						}
					case "tool_use":
						turnHasToolUse = true
					}
				}
			}
		case "user":
			// A "user" event from the CLI's own stream is a tool_result
			// echo. That signals the previous LLM turn ended; close it.
			if len(ev.ToolUseResult) > 0 || strings.Contains(string(ev.Message), "tool_use_id") {
				closeTurn()
				boundary = time.Now().UnixNano()
			}
		case "result":
			// Final boundary. Close any in-flight turn and tag the root.
			closeTurn()
			sawResult = true
			root.Set("agent.num_turns", ev.NumTurns)
			root.Set("agent.duration_ms", ev.DurationMS)
			root.Set("agent.duration_api_ms", ev.DurationAPIMS)
			root.Set("agent.total_cost_usd", ev.TotalCostUSD)
			root.Set("agent.is_error", ev.IsError)
			root.Set("agent.truncated", ev.NumTurns >= cliMaxTurns && ev.IsError)
			if ev.IsError {
				emitErr = fmt.Errorf("CLI result is_error: subtype=%s result=%s",
					ev.Subtype, truncate(ev.Result, 200))
			}
			if ev.Result != "" && len(finalChunks) == 0 {
				finalChunks = append(finalChunks, ev.Result)
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return strings.Join(finalChunks, "\n"), err
	}
	if !sawResult {
		// belt-and-suspenders: close any orphan turn so we don't lose the span
		closeTurn()
		return strings.Join(finalChunks, "\n"),
			fmt.Errorf("CLI stream ended without a result message")
	}
	return strings.Join(finalChunks, "\n"), emitErr
}

