package sweagent

// In-process MCP HTTP server hosting the three Go SWE-Agent tools so the
// Claude CLI subprocess (driven by RunWithCLI) can call them. Mirrors
// benchmark-go/internal/chemcrow/mcp_server.go almost line-for-line — only
// the tool specs and the dispatch differ.
//
// Per-query token routing: at concurrency N the Go process has N concurrent
// agent goroutines, each with its own (Observer, workspace_dir) pair. A
// shared MCP endpoint can't tell which trace a tool/call belongs to without
// the token. We register (token -> handle) on Root() and unregister on
// agent finish.

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const mcpProtocolVersion = "2024-11-05"

var mcpToolSpecs = []map[string]any{
	{
		"name": "bash_run",
		"description": "Run a bash command inside the workspace directory. " +
			"Returns {stdout, stderr, exit_code, timed_out}. Default timeout " +
			"is 30s. Use shell metacharacters freely (|, >, &&) — they are " +
			"wrapped with bash -c automatically.",
		"inputSchema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"command": map[string]any{"type": "string"},
			},
			"required": []string{"command"},
		},
	},
	{
		"name": "read_file",
		"description": "Read up to 50 KB from a file inside the workspace. Returns " +
			"{content, truncated, size_bytes}. Path is relative to the workspace.",
		"inputSchema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path": map[string]any{"type": "string"},
			},
			"required": []string{"path"},
		},
	},
	{
		"name": "write_file",
		"description": "Write content to a file inside the workspace (overwrites). " +
			"Creates parent directories. Path is relative to the workspace.",
		"inputSchema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path":    map[string]any{"type": "string"},
				"content": map[string]any{"type": "string"},
			},
			"required": []string{"path", "content"},
		},
	},
}

// agentHandle holds everything a tool/call needs: the Observer to emit spans
// on, and the workspace_dir to run the bash command in / resolve paths
// against.
type agentHandle struct {
	obs       *obs.Observer
	workspace string
}

var (
	mcpInitOnce sync.Once
	mcpInitErr  error
	mcpListener net.Listener
	mcpRegistry sync.Map // token (string) -> *agentHandle
)

func ensureMCPHost() (string, error) {
	mcpInitOnce.Do(func() {
		l, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			mcpInitErr = err
			return
		}
		mcpListener = l
		mux := http.NewServeMux()
		mux.HandleFunc("/mcp/", handleMCP)
		srv := &http.Server{
			Handler:      mux,
			ReadTimeout:  60 * time.Second,
			WriteTimeout: 120 * time.Second,
		}
		go func() {
			_ = srv.Serve(l)
		}()
	})
	if mcpInitErr != nil {
		return "", mcpInitErr
	}
	return "http://" + mcpListener.Addr().String(), nil
}

// registerHandle binds (observer, workspace) to a fresh token in the registry
// and returns (token, mcp_endpoint_url, cleanup).
func registerHandle(o *obs.Observer, workspace string) (string, string, func(), error) {
	base, err := ensureMCPHost()
	if err != nil {
		return "", "", nil, err
	}
	tokBytes := make([]byte, 8)
	if _, err := rand.Read(tokBytes); err != nil {
		return "", "", nil, err
	}
	token := hex.EncodeToString(tokBytes)
	mcpRegistry.Store(token, &agentHandle{obs: o, workspace: workspace})
	return token, base + "/mcp/" + token, func() { mcpRegistry.Delete(token) }, nil
}

// JSON-RPC handling (mirrors chemcrow/mcp_server.go).

type jsonrpcReq struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type jsonrpcResp struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  any             `json:"result,omitempty"`
	Error   *jsonrpcError   `json:"error,omitempty"`
}

type jsonrpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func handleMCP(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/mcp/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		http.Error(w, "missing token", http.StatusNotFound)
		return
	}
	token := parts[0]
	handleAny, ok := mcpRegistry.Load(token)
	if !ok {
		http.Error(w, "unknown token", http.StatusNotFound)
		return
	}
	handle := handleAny.(*agentHandle)

	switch r.Method {
	case http.MethodPost:
		// fall through
	case http.MethodGet:
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(http.StatusOK)
		return
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	var req jsonrpcReq
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			Error:   &jsonrpcError{Code: -32700, Message: "parse error: " + err.Error()},
		})
		return
	}

	isNotification := len(req.ID) == 0 || string(req.ID) == "null"

	switch req.Method {
	case "initialize":
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result: map[string]any{
				"protocolVersion": mcpProtocolVersion,
				"capabilities":    map[string]any{"tools": map[string]any{}},
				"serverInfo":      map[string]any{"name": "sweagent_go", "version": "1.0.0"},
			},
		})
	case "notifications/initialized", "initialized":
		w.WriteHeader(http.StatusAccepted)
	case "tools/list":
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result:  map[string]any{"tools": mcpToolSpecs},
		})
	case "tools/call":
		var p struct {
			Name      string          `json:"name"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if err := json.Unmarshal(req.Params, &p); err != nil {
			writeJSON(w, http.StatusOK, jsonrpcResp{
				JSONRPC: "2.0",
				ID:      req.ID,
				Error:   &jsonrpcError{Code: -32602, Message: "bad params: " + err.Error()},
			})
			return
		}
		text, callErr := dispatchMCPTool(p.Name, p.Arguments, handle)
		if callErr != nil {
			writeJSON(w, http.StatusOK, jsonrpcResp{
				JSONRPC: "2.0",
				ID:      req.ID,
				Result: map[string]any{
					"isError": true,
					"content": []map[string]any{{"type": "text", "text": "tool error: " + callErr.Error()}},
				},
			})
			return
		}
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result: map[string]any{
				"content": []map[string]any{{"type": "text", "text": text}},
			},
		})
	case "ping":
		writeJSON(w, http.StatusOK, jsonrpcResp{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{}})
	default:
		if isNotification {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error:   &jsonrpcError{Code: -32601, Message: "method not found: " + req.Method},
		})
	}
}

func dispatchMCPTool(name string, raw json.RawMessage, h *agentHandle) (string, error) {
	switch name {
	case "bash_run":
		var args struct {
			Command        string `json:"command"`
			TimeoutSeconds int    `json:"timeout_seconds,omitempty"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := BashRun(args.Command, h.workspace, args.TimeoutSeconds, h.obs)
		return jsonString(out), nil
	case "read_file":
		var args struct {
			Path string `json:"path"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := ReadFile(args.Path, h.workspace, h.obs)
		return jsonString(out), nil
	case "write_file":
		var args struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := WriteFile(args.Path, args.Content, h.workspace, h.obs)
		return jsonString(out), nil
	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}
