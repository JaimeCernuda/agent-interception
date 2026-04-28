package chemcrow

// In-process MCP server that hosts the three Go-side chemistry tools so the
// Claude CLI subprocess (driven by RunWithCLI) can call them via JSON-RPC over
// HTTP. The server is started once per Go process and routes incoming
// tool/call requests to the per-query Observer via a registry keyed by a
// random token embedded in the URL path.
//
// Why HTTP and not stdio: stdio would require the CLI to fork a SEPARATE Go
// MCP-server subprocess, which would not share the agent's Observer (so tool
// spans couldn't land in the right trace tree). HTTP keeps everything in one
// Go process.
//
// Why per-query tokens: at concurrency N the Go process has N concurrent
// agent goroutines, each with its own Observer. A single shared MCP endpoint
// would have no way to know which trace a given tool/call belongs to. The
// token in the URL identifies the (query, observer) pair.

import (
	"context"
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

// mcpToolSpecs are advertised to the CLI via tools/list. They MUST mirror the
// Python config's @tool decorators byte-for-byte (descriptions and schemas)
// so a model trained on either side picks the same tool.
var mcpToolSpecs = []map[string]any{
	{
		"name": "lookup_molecule",
		"description": "Look up a molecule by common name on PubChem and return its " +
			"canonical SMILES and molecular weight. Use this first to get a " +
			"SMILES string from a name like 'aspirin' or 'paclitaxel'.",
		"inputSchema": map[string]any{
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
		"inputSchema": map[string]any{
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
		"inputSchema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"smiles": map[string]any{"type": "string"}},
			"required":   []string{"smiles"},
		},
	},
}

var (
	mcpInitOnce sync.Once
	mcpInitErr  error
	mcpListener net.Listener
	mcpRegistry sync.Map // token (string) -> *obs.Observer
)

// ensureMCPHost starts the in-process MCP HTTP server on a random localhost
// port if it hasn't started yet. Returns the base URL like "http://127.0.0.1:54321".
// Concurrency-safe; subsequent callers see the cached listener.
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

// registerObserver allocates a fresh token, binds the supplied observer to it
// in the registry, and returns (token, mcp_endpoint_url, cleanup).
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

// ---------------------------------------------------------------------------
// JSON-RPC + MCP request handling
// ---------------------------------------------------------------------------

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
	observer := obsAny.(*obs.Observer)

	// MCP HTTP transport (2024-11-05 spec): POST = client request, GET = SSE.
	// We don't initiate server-to-client messages, so GET is a no-op stream.
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

	// Notifications have null id and require no response per JSON-RPC spec.
	isNotification := len(req.ID) == 0 || string(req.ID) == "null"

	switch req.Method {
	case "initialize":
		writeJSON(w, http.StatusOK, jsonrpcResp{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result: map[string]any{
				"protocolVersion": mcpProtocolVersion,
				"capabilities":    map[string]any{"tools": map[string]any{}},
				"serverInfo":      map[string]any{"name": "chemcrow_go", "version": "1.0.0"},
			},
		})
	case "notifications/initialized", "initialized":
		// Pure notification: 202 Accepted with empty body.
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
		text, callErr := dispatchMCPTool(p.Name, p.Arguments, observer)
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

// dispatchMCPTool routes tools/call to the existing instrumented Go tool funcs.
// Each tool emits its own tool.* span on the supplied Observer (which the
// agent goroutine opened the root span on), so the resulting trace stays
// nested correctly.
func dispatchMCPTool(name string, raw json.RawMessage, o *obs.Observer) (string, error) {
	switch name {
	case "lookup_molecule":
		var args struct {
			Name string `json:"name"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := LookupMolecule(args.Name, o)
		return jsonString(out), nil
	case "smiles_to_3d":
		var args struct {
			SMILES string `json:"smiles"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := SmilesTo3D(args.SMILES, o)
		return jsonString(out), nil
	case "compute_descriptors":
		var args struct {
			SMILES string `json:"smiles"`
		}
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", err
		}
		out := ComputeDescriptors(args.SMILES, o)
		return jsonString(out), nil
	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}

// shutdownMCPServer is currently unused; kept exported-internal for tests
// that might want to tear down the listener between cases.
func shutdownMCPServer(ctx context.Context) error { //nolint:unused
	if mcpListener != nil {
		_ = mcpListener.Close()
	}
	_ = ctx
	return nil
}
