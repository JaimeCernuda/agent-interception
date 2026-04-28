package sweagent

// RunWithCLI drives one SWE-Agent query via the Claude CLI subprocess
// (Pro-plan tokens), with the in-process MCP HTTP server (mcp_server.go)
// hosting the three Go tools. Mirrors benchmark/configs/config_sweagent_py.py
// _run_async timing logic: one llm.generate span per real API turn,
// aggregated from the CLI's stream-json AssistantMessage chunks.

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
	"syscall"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const (
	cliBin            = "claude"
	cliMaxTurns       = 30
	cliRequestTimeout = 15 * time.Minute
	cliModelDefault   = "claude-haiku-4-5"
)

const SystemPrompt = "You are a software-engineering assistant working in a workspace directory. " +
	"You have three tools:\n" +
	"  - bash_run: run a shell command. Use this for grep, awk, ls, head, find, " +
	"and any custom test runners (e.g. `bash runtests.sh`).\n" +
	"  - read_file: read a file's contents.\n" +
	"  - write_file: write a file's contents (overwrites).\n" +
	"Always use these tools — never claim to have run a command without calling " +
	"bash_run. After running tools, REASON over their output: parse, aggregate, " +
	"filter, then answer. Be concise in your final answer."

// Query is one entry from sweagent_20.json.
type Query struct {
	QueryID                string `json:"query_id"`
	Category               string `json:"category"`
	Label                  string `json:"label"`
	WorkspaceDir           string `json:"workspace_dir"`
	QueryText              string `json:"query_text"`
	ReferenceCallCount     int    `json:"reference_call_count,omitempty"`
	ExpectedOutputPattern  string `json:"expected_output_pattern,omitempty"`
}

type cliEvent struct {
	Type    string          `json:"type"`
	Message json.RawMessage `json:"message,omitempty"`
	Subtype string          `json:"subtype,omitempty"`
	UUID    string          `json:"uuid,omitempty"`
	ToolUseResult json.RawMessage `json:"tool_use_result,omitempty"`
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

func model() string {
	if v := os.Getenv("ANTHROPIC_MODEL"); v != "" {
		return v
	}
	return cliModelDefault
}

// measureSelfCPUMs returns the calling process's user+system CPU time in ms.
// Same semantics as the Python config's resource.getrusage(RUSAGE_SELF).
func measureSelfCPUMs() float64 {
	var r syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &r); err != nil {
		return 0
	}
	user := float64(r.Utime.Sec)*1000 + float64(r.Utime.Usec)/1000.0
	sys := float64(r.Stime.Sec)*1000 + float64(r.Stime.Usec)/1000.0
	return user + sys
}

// RunWithCLI is the entry point for Pro-plan Go runs. Returns the final
// assistant text. Side-effect: emits the agent.query trace via `o`, with
// llm.generate + tool.* children, and tags the root with agent.cpu_time_ms.
func RunWithCLI(q Query, workspaceDir string, o *obs.Observer) (string, error) {
	if _, err := exec.LookPath(cliBin); err != nil {
		return "", fmt.Errorf("claude CLI not found in PATH: %w", err)
	}
	_, mcpURL, cleanup, err := registerHandle(o, workspaceDir)
	if err != nil {
		return "", fmt.Errorf("mcp host: %w", err)
	}
	defer cleanup()

	mcpConfig := map[string]any{
		"mcpServers": map[string]any{
			"sweagent": map[string]any{
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
		"--system-prompt", SystemPrompt,
		"--mcp-config", string(mcpJSON),
		"--allowed-tools",
		"mcp__sweagent__bash_run," +
			"mcp__sweagent__read_file," +
			"mcp__sweagent__write_file",
		"--permission-mode", "bypassPermissions",
	}

	ctx, cancel := context.WithTimeout(context.Background(), cliRequestTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, cliBin, args...)
	cmd.Dir = workspaceDir

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

	cpuStart := measureSelfCPUMs()
	root := o.Root("agent.query", map[string]any{
		"query_text":            q.QueryText,
		"agent.workspace_dir":   workspaceDir,
		"sweagent.category":     q.Category,
		"sweagent.label":        q.Label,
	})
	defer func() {
		root.Set("agent.cpu_time_ms", measureSelfCPUMs()-cpuStart)
		root.End()
	}()

	if err := cmd.Start(); err != nil {
		root.Set("agent.error", err.Error())
		root.Fail(err)
		return "", err
	}

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
		root.Set("agent.cli_exit_error", waitErr.Error())
	}
	return finalText, nil
}

func consumeStream(stdout io.Reader, o *obs.Observer, root *obs.Span) (string, error) {
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 1<<20), 8<<20)

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
			continue
		}
		switch ev.Type {
		case "system", "rate_limit_event", "stream_event":
			// nothing to attribute
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
			if len(ev.ToolUseResult) > 0 || strings.Contains(string(ev.Message), "tool_use_id") {
				closeTurn()
				boundary = time.Now().UnixNano()
			}
		case "result":
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
		closeTurn()
		return strings.Join(finalChunks, "\n"),
			fmt.Errorf("CLI stream ended without a result message")
	}
	return strings.Join(finalChunks, "\n"), emitErr
}
