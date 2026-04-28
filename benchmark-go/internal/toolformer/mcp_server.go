package toolformer

// In-process MCP HTTP server hosting the single Go Toolformer tool
// (calculator) so the Claude CLI subprocess (driven by RunWithCLI) can call
// it. Mirrors benchmark-go/internal/sweagent/mcp_server.go almost
// line-for-line — only the tool spec and dispatch differ.
//
// Per-query token routing: at concurrency N the Go process has N concurrent
// agent goroutines, each with its own Observer. A shared MCP endpoint can't
// tell which trace a tool/call belongs to without the token. We register
// (token -> Observer) on Root() and unregister on agent finish.

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
		"name": "calculator",
		"description": "Evaluate a numeric arithmetic expression and return the result. " +
			"Supports +, -, *, /, **, parentheses, and the math functions sqrt, " +
			"log, exp, sin, cos, tan, abs, min, max, pow. Constants pi and e are " +
			"available. Returns {result: float, error: string|null}. On error " +
			"(invalid expression, division by zero, etc.) result is null and " +
			"error describes the problem; the caller can retry with a corrected " +
			"expression.",
		"inputSchema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"expression": map[string]any{"type": "string"},
			},
			"required": []string{"expression"},
		},
	},
}

var (
	mcpInitOnce sync.Once
	mcpInitErr  error
	mcpListener net.Listener
	mcpRegistry sync.Map // token (string) -> *obs.Observer
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

// registerObserver binds an Observer to a fresh token in the registry and
// returns (token, mcp_endpoint_url, cleanup).
func registerObserver(o *obs.Observer) (string, string, func(), error) {
	base, err := ensureMCPHost()
	if err != nil {
		return "", "", nil, err
	}
	tokBytes := make([]byte, 8)
	if _, err := rand.Read(tokBytes); err != nil {
		return "", "", nil, err
	}
	token := hex.EncodeToString(tokBytes)
	mcpRegistry.Store(token, o)
	return token, base + "/mcp/" + token, func() { mcpRegistry.Delete(token) }, nil
}

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
	obsAny, ok := mcpRegistry.Load(token)
	if !ok {
		http.Error(w, "unknown token", http.StatusNotFound)
		return
	}
	o := obsAny.(*obs.Observer)

	switch r.Method {
	case http.MethodPost:
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
				"serverInfo":      map[string]any{"name": "toolformer_go", "version": "1.0.0"},
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
		text, callErr := dispatchMCPTool(p.Name, p.Arguments, o)
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

func dispatchMCPTool(name string, raw json.RawMessage, o *obs.Observer) (string, error) {
	switch name {
	case "calculator":
		var args struct {
			Expression string `json:"expression"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := Calculator(args.Expression, o)
		return jsonString(out), nil
	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}
