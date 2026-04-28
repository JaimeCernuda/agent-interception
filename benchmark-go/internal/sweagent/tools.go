// Package sweagent holds the Go-side SWE-Agent tool implementations and the
// CLI-driven agent loop. Span vocabulary (tool.bash_run / tool.bash_spawn /
// tool.bash_work / tool.read_file / tool.write_file) matches the Python
// config so cross-language tests stay green.
package sweagent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const (
	stdioCapBytes      = 10 * 1024
	defaultTimeoutSecs = 30
	maxTimeoutSecs     = 30
	readFileCapBytes   = 50 * 1024
)

// shellMetacharacters force a `bash -c <cmd>` wrap. Detect via substring scan
// (NOT a tokenized parse): redirection metachars don't survive shlex-style
// tokenization either way, so substring is faithful.
var shellMetacharacters = []string{"&&", "||", ";", "|", ">", "<"}

func shouldShellWrap(cmd string) bool {
	for _, m := range shellMetacharacters {
		if strings.Contains(cmd, m) {
			return true
		}
	}
	return false
}

// safeWorkspacePath resolves rawPath against workspaceDir and refuses to
// escape it (no parent traversal, no absolute paths outside).
func safeWorkspacePath(workspaceDir, rawPath string) (string, error) {
	wd, err := filepath.Abs(workspaceDir)
	if err != nil {
		return "", err
	}
	wd = filepath.Clean(wd)
	cand := filepath.Clean(filepath.Join(wd, rawPath))
	rel, err := filepath.Rel(wd, cand)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path %q escapes workspace %s", rawPath, wd)
	}
	return cand, nil
}

func truncateBytes(data []byte, cap int) (string, bool) {
	if len(data) <= cap {
		return string(data), false
	}
	return string(data[:cap]) + "\n... [truncated]", true
}

// BashResult is the JSON shape returned to the LLM by bash_run.
type BashResult struct {
	Stdout           string `json:"stdout"`
	Stderr           string `json:"stderr"`
	ExitCode         int    `json:"exit_code"`
	TimedOut         bool   `json:"timed_out"`
	StdoutTruncated  bool   `json:"stdout_truncated"`
	StderrTruncated  bool   `json:"stderr_truncated"`
}

// BashRun executes `command` inside `workspaceDir` and emits the
// tool.bash_run / tool.bash_spawn / tool.bash_work span tree on `o`.
func BashRun(command, workspaceDir string, timeoutSeconds int, o *obs.Observer) BashResult {
	if timeoutSeconds <= 0 {
		timeoutSeconds = defaultTimeoutSecs
	}
	if timeoutSeconds > maxTimeoutSecs {
		timeoutSeconds = maxTimeoutSecs
	}

	shellWrapped := shouldShellWrap(command)
	preview := command
	if len(preview) > 200 {
		preview = preview[:200]
	}

	outer := o.Start("tool.bash_run", map[string]any{
		"tool.name":             "bash_run",
		"tool.input_hash":       inputHash(command),
		"bash.command_preview":  preview,
		"bash.shell_wrapped":    shellWrapped,
		"bash.timeout_seconds":  timeoutSeconds,
	})
	defer outer.End()

	var args []string
	if shellWrapped {
		args = []string{"/bin/bash", "-c", command}
	} else {
		tokens, err := tokenize(command)
		if err != nil || len(tokens) == 0 {
			outer.Set("tool.error", "argv_parse")
			return BashResult{
				Stdout: "", Stderr: "argv parse error",
				ExitCode: -1, TimedOut: false,
			}
		}
		args = tokens
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSeconds)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir = workspaceDir
	stdoutPipe, _ := cmd.StdoutPipe()
	stderrPipe, _ := cmd.StderrPipe()

	// ---- bash_spawn span ----
	spawnStart := time.Now().UnixNano()
	cpuSpawnStart := time.Now().UnixNano() // realClock CPUNs == wall on Go side
	startErr := cmd.Start()
	spawnEnd := time.Now().UnixNano()
	cpuSpawnEnd := spawnEnd
	if startErr != nil {
		o.EmitSyntheticSpanCPU(outer, "tool.bash_spawn",
			spawnStart, spawnEnd, cpuSpawnStart, cpuSpawnEnd,
			map[string]any{
				"tool.name":          "bash_spawn",
				"bash.spawn_error":   startErr.Error(),
				"bash.shell_wrapped": shellWrapped,
			})
		outer.Set("tool.error", "spawn: "+startErr.Error())
		return BashResult{
			Stdout: "", Stderr: "spawn error: " + startErr.Error(),
			ExitCode: -1, TimedOut: false,
		}
	}
	o.EmitSyntheticSpanCPU(outer, "tool.bash_spawn",
		spawnStart, spawnEnd, cpuSpawnStart, cpuSpawnEnd,
		map[string]any{
			"tool.name":          "bash_spawn",
			"bash.shell_wrapped": shellWrapped,
			"bash.pid":           cmd.Process.Pid,
		})

	// ---- bash_work span ----
	workStart := spawnEnd
	cpuWorkStart := cpuSpawnEnd
	stdoutBytes, _ := io.ReadAll(stdoutPipe)
	stderrBytes, _ := io.ReadAll(stderrPipe)
	waitErr := cmd.Wait()
	workEnd := time.Now().UnixNano()
	cpuWorkEnd := workEnd

	timedOut := false
	exitCode := 0
	if waitErr != nil {
		if ctx.Err() == context.DeadlineExceeded {
			timedOut = true
			exitCode = -9
		} else if ee, ok := waitErr.(*exec.ExitError); ok {
			exitCode = ee.ExitCode()
		} else {
			exitCode = -1
		}
	}

	o.EmitSyntheticSpanCPU(outer, "tool.bash_work",
		workStart, workEnd, cpuWorkStart, cpuWorkEnd,
		map[string]any{
			"tool.name":         "bash_work",
			"bash.exit_code":    exitCode,
			"bash.timed_out":    timedOut,
			"bash.stdout_bytes": len(stdoutBytes),
			"bash.stderr_bytes": len(stderrBytes),
		})

	stdoutTrunc, stdoutWasTrunc := truncateBytes(stdoutBytes, stdioCapBytes)
	stderrTrunc, stderrWasTrunc := truncateBytes(stderrBytes, stdioCapBytes)
	outer.Set("bash.exit_code", exitCode)
	outer.Set("bash.timed_out", timedOut)
	outer.Set("bash.stdout_bytes", len(stdoutBytes))
	outer.Set("bash.stderr_bytes", len(stderrBytes))
	outer.Set("bash.stdout_truncated", stdoutWasTrunc)
	outer.Set("bash.stderr_truncated", stderrWasTrunc)
	return BashResult{
		Stdout:          stdoutTrunc,
		Stderr:          stderrTrunc,
		ExitCode:        exitCode,
		TimedOut:        timedOut,
		StdoutTruncated: stdoutWasTrunc,
		StderrTruncated: stderrWasTrunc,
	}
}

// ReadFileResult is the JSON shape returned to the LLM by read_file.
type ReadFileResult struct {
	Content   string `json:"content"`
	Truncated bool   `json:"truncated"`
	SizeBytes int    `json:"size_bytes"`
	Error     string `json:"error,omitempty"`
}

func ReadFile(path, workspaceDir string, o *obs.Observer) ReadFileResult {
	s := o.Start("tool.read_file", map[string]any{
		"tool.name":       "read_file",
		"tool.input_hash": inputHash(path),
		"tool.path":       path,
	})
	defer s.End()

	target, err := safeWorkspacePath(workspaceDir, path)
	if err != nil {
		s.Set("tool.error", "path: "+err.Error())
		return ReadFileResult{Error: err.Error()}
	}
	data, err := os.ReadFile(target)
	if err != nil {
		if os.IsNotExist(err) {
			s.Set("tool.error", "not_found")
			return ReadFileResult{Error: "not_found"}
		}
		s.Set("tool.error", "io: "+err.Error())
		return ReadFileResult{Error: "io: " + err.Error()}
	}
	size := len(data)
	text, truncated := truncateBytes(data, readFileCapBytes)
	s.Set("tool.size_bytes", size)
	s.Set("tool.truncated", truncated)
	return ReadFileResult{Content: text, Truncated: truncated, SizeBytes: size}
}

// WriteFileResult is the JSON shape returned to the LLM by write_file.
type WriteFileResult struct {
	OK        bool   `json:"ok"`
	SizeBytes int    `json:"size_bytes"`
	Error     string `json:"error,omitempty"`
}

func WriteFile(path, content, workspaceDir string, o *obs.Observer) WriteFileResult {
	s := o.Start("tool.write_file", map[string]any{
		"tool.name":       "write_file",
		"tool.input_hash": inputHash(path),
		"tool.path":       path,
	})
	defer s.End()

	target, err := safeWorkspacePath(workspaceDir, path)
	if err != nil {
		s.Set("tool.error", "path: "+err.Error())
		return WriteFileResult{Error: err.Error()}
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		s.Set("tool.error", "io: "+err.Error())
		return WriteFileResult{Error: "io: " + err.Error()}
	}
	data := []byte(content)
	if err := os.WriteFile(target, data, 0o644); err != nil {
		s.Set("tool.error", "io: "+err.Error())
		return WriteFileResult{Error: "io: " + err.Error()}
	}
	s.Set("tool.size_bytes", len(data))
	return WriteFileResult{OK: true, SizeBytes: len(data)}
}

// inputHash matches the Python obs.input_hash signature: sha256 of input
// bytes, first 16 hex chars.
func inputHash(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])[:16]
}

func jsonString(v any) string {
	buf, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf(`{"error":"json_marshal: %s"}`, err.Error())
	}
	return string(buf)
}

// tokenize splits a shell-like command into argv tokens. Supports double and
// single quotes; does NOT support shell expansion (intentional — that's the
// shell-wrap path). Mirrors Python's shlex.split for non-meta commands.
func tokenize(s string) ([]string, error) {
	var out []string
	var cur strings.Builder
	inSingle, inDouble, escape := false, false, false
	for _, r := range s {
		if escape {
			cur.WriteRune(r)
			escape = false
			continue
		}
		switch {
		case r == '\\' && !inSingle:
			escape = true
		case r == '\'' && !inDouble:
			inSingle = !inSingle
		case r == '"' && !inSingle:
			inDouble = !inDouble
		case (r == ' ' || r == '\t') && !inSingle && !inDouble:
			if cur.Len() > 0 {
				out = append(out, cur.String())
				cur.Reset()
			}
		default:
			cur.WriteRune(r)
		}
	}
	if inSingle || inDouble {
		return nil, fmt.Errorf("unclosed quote")
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
