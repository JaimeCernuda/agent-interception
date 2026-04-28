// Package chemcrow holds the Go-side tool implementations and the tool-use
// loop driver for the ChemCrow benchmark.
//
// Three tools — names, span attrs, and JSON output schemas all match the
// Python config (benchmark/configs/config_chemcrow_py.py) so cross-language
// trace-shape diffs (tests/test_cross_lang/) stay green.
package chemcrow

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const pubchemBase = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

// pythonExe is the interpreter the smiles_to_3d / compute_descriptors tools
// shell out to. Override via CHEMCROW_PYTHON env var (used by the cross-lang
// test to point at the venv).
func pythonExe() string {
	if v := os.Getenv("CHEMCROW_PYTHON"); v != "" {
		return v
	}
	return "python3"
}

// rdkitScript returns the absolute path to the RDKit subprocess script.
// Honors CHEMCROW_RDKIT_SCRIPT for tests; otherwise resolves relative to this
// source file's compile-time location (works for `go run` and `go build`).
func rdkitScript() string {
	if v := os.Getenv("CHEMCROW_RDKIT_SCRIPT"); v != "" {
		return v
	}
	_, thisFile, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "scripts", "rdkit_3d.py")
}

// pubchemCacheDir is shared with the Python config: same on-disk cache so a
// repeated run from either language is a hit.
func pubchemCacheDir() string {
	if v := os.Getenv("CHEMCROW_PUBCHEM_CACHE"); v != "" {
		return v
	}
	_, thisFile, _, _ := runtime.Caller(0)
	return filepath.Clean(
		filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "benchmark", "cache", "pubchem"),
	)
}

// ----------------------------------------------------------------------
// lookup_molecule
// ----------------------------------------------------------------------

// LookupResult is the JSON shape returned by lookupMolecule, both to the LLM
// and to its callers in this package.
type LookupResult struct {
	Name             string  `json:"name"`
	SMILES           string  `json:"smiles"`
	MolecularWeight  float64 `json:"molecular_weight"`
}

func LookupMolecule(name string, o *obs.Observer) LookupResult {
	s := o.Start("tool.lookup_molecule", map[string]any{
		"tool.name":          "lookup_molecule",
		"tool.input_hash":    inputHash(name),
		"tool.molecule_name": name,
		"tool.retry_count":   0,
	})
	defer s.End()

	cachePath := filepath.Join(pubchemCacheDir(), safeFilename(name)+".json")
	if data, err := os.ReadFile(cachePath); err == nil {
		var cached LookupResult
		if jerr := json.Unmarshal(data, &cached); jerr == nil {
			s.Set("tool.cache_hit", true)
			s.Set("tool.smiles", cached.SMILES)
			s.Set("tool.molecular_weight", cached.MolecularWeight)
			s.Set("tool.output_size_bytes", len(data))
			return cached
		}
	}

	s.Set("tool.cache_hit", false)
	url := fmt.Sprintf(
		"%s/compound/name/%s/property/CanonicalSMILES,MolecularWeight/JSON",
		pubchemBase,
		urlQuote(name),
	)
	body, retries, status := httpGetJSON(url, 2)
	s.Set("tool.retry_count", retries)
	s.Set("tool.http_status", status)

	smiles, mw := extractPubchem(body)
	out := LookupResult{Name: name, SMILES: smiles, MolecularWeight: mw}
	if smiles != "" {
		_ = os.MkdirAll(pubchemCacheDir(), 0o755)
		if buf, err := json.MarshalIndent(out, "", "  "); err == nil {
			_ = os.WriteFile(cachePath, buf, 0o644)
		}
	}
	s.Set("tool.smiles", smiles)
	s.Set("tool.molecular_weight", mw)
	if buf, err := json.Marshal(out); err == nil {
		s.Set("tool.output_size_bytes", len(buf))
	}
	return out
}

// ----------------------------------------------------------------------
// smiles_to_3d
// ----------------------------------------------------------------------

type Conformer3DResult struct {
	SMILES                  string   `json:"smiles"`
	OK                      bool     `json:"ok"`
	NumAtoms                int      `json:"num_atoms"`
	NumHeavyAtoms           int      `json:"num_heavy_atoms"`
	Energy                  *float64 `json:"energy"`
	EmbedAttempts           int      `json:"embed_attempts"`
	OptimizationStatus      int      `json:"optimization_status"`
	OptimizationIterations  int      `json:"optimization_iterations"`
	Error                   string   `json:"error,omitempty"`
}

func SmilesTo3D(smiles string, o *obs.Observer) Conformer3DResult {
	s := o.Start("tool.smiles_to_3d", map[string]any{
		"tool.name":        "smiles_to_3d",
		"tool.input_hash":  inputHash(smiles),
		"tool.smiles":      smiles,
		"tool.retry_count": 0,
	})
	defer s.End()

	body, _ := json.Marshal(map[string]any{"smiles": smiles, "op": "smiles_to_3d"})
	out, startupMs, runErr := runRDKit(body)
	s.Set("subprocess.startup_ms", startupMs)
	if runErr != nil {
		s.Fail(runErr)
		return Conformer3DResult{SMILES: smiles, OK: false, Error: runErr.Error()}
	}
	var result Conformer3DResult
	if err := json.Unmarshal(out, &result); err != nil {
		s.Fail(err)
		return Conformer3DResult{SMILES: smiles, OK: false, Error: err.Error()}
	}
	s.Set("rdkit.embed_attempts", result.EmbedAttempts)
	s.Set("rdkit.optimization_status", result.OptimizationStatus)
	s.Set("rdkit.optimization_iterations", result.OptimizationIterations)
	if result.Energy != nil {
		s.Set("tool.energy", *result.Energy)
	}
	s.Set("tool.num_atoms", result.NumAtoms)
	s.Set("tool.heavy_atom_count", result.NumHeavyAtoms)
	if !result.OK && result.Error != "" {
		s.Set("tool.error", result.Error)
	}
	s.Set("tool.output_size_bytes", len(out))
	return result
}

// ----------------------------------------------------------------------
// compute_descriptors
// ----------------------------------------------------------------------

type DescriptorsResult struct {
	SMILES            string  `json:"smiles"`
	OK                bool    `json:"ok"`
	MolecularWeight   float64 `json:"molecular_weight"`
	LogP              float64 `json:"logp"`
	TPSA              float64 `json:"tpsa"`
	HeavyAtomCount    int     `json:"heavy_atom_count"`
	NumRotatableBonds int     `json:"num_rotatable_bonds"`
	Error             string  `json:"error,omitempty"`
}

func ComputeDescriptors(smiles string, o *obs.Observer) DescriptorsResult {
	s := o.Start("tool.compute_descriptors", map[string]any{
		"tool.name":        "compute_descriptors",
		"tool.input_hash":  inputHash(smiles),
		"tool.smiles":      smiles,
		"tool.retry_count": 0,
	})
	defer s.End()

	body, _ := json.Marshal(map[string]any{"smiles": smiles, "op": "descriptors"})
	out, startupMs, runErr := runRDKit(body)
	s.Set("subprocess.startup_ms", startupMs)
	if runErr != nil {
		s.Fail(runErr)
		return DescriptorsResult{SMILES: smiles, OK: false, Error: runErr.Error()}
	}
	var result DescriptorsResult
	if err := json.Unmarshal(out, &result); err != nil {
		s.Fail(err)
		return DescriptorsResult{SMILES: smiles, OK: false, Error: err.Error()}
	}
	s.Set("tool.molecular_weight", result.MolecularWeight)
	s.Set("tool.logp", result.LogP)
	s.Set("tool.tpsa", result.TPSA)
	s.Set("tool.heavy_atom_count", result.HeavyAtomCount)
	s.Set("tool.num_rotatable_bonds", result.NumRotatableBonds)
	s.Set("tool.output_size_bytes", len(out))
	return result
}

// ----------------------------------------------------------------------
// helpers
// ----------------------------------------------------------------------

// runRDKit shells out to the rdkit_3d.py subprocess. Returns the JSON output,
// the measured subprocess.startup_ms (time from cmd.Start() to first stdout
// byte / process exit, whichever comes first), and an error.
func runRDKit(body []byte) (output []byte, startupMs float64, err error) {
	scriptPath := rdkitScript()
	if _, statErr := os.Stat(scriptPath); statErr != nil {
		return nil, 0, fmt.Errorf("rdkit script not found at %s: %w", scriptPath, statErr)
	}
	cmd := exec.Command(pythonExe(), scriptPath)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, 0, err
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	startedAt := time.Now()
	if err := cmd.Start(); err != nil {
		return nil, 0, err
	}
	// Subprocess startup is "time from Start() to stdin/stdout pipes wired and
	// process accepting input". We measure: write the input, close stdin,
	// then capture the elapsed time before Wait() (which blocks until exit).
	pipesReadyAt := time.Now()
	if _, werr := stdin.Write(body); werr != nil {
		_ = cmd.Process.Kill()
		return nil, 0, werr
	}
	if cerr := stdin.Close(); cerr != nil {
		_ = cmd.Process.Kill()
		return nil, 0, cerr
	}
	startupMs = float64(pipesReadyAt.Sub(startedAt).Microseconds()) / 1e3
	if werr := cmd.Wait(); werr != nil {
		// Non-zero exit is allowed if stdout still contains valid JSON
		// (smiles_to_3d returns exit 1 on invalid SMILES with a JSON body).
		if stdout.Len() == 0 {
			return nil, startupMs, fmt.Errorf("rdkit subprocess: %w (stderr=%s)",
				werr, truncate(stderr.String(), 200))
		}
	}
	return stdout.Bytes(), startupMs, nil
}

func httpGetJSON(url string, maxRetries int) ([]byte, int, int) {
	client := &http.Client{Timeout: 15 * time.Second}
	var lastStatus int
	retries := 0
	for attempt := 0; attempt <= maxRetries; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			cancel()
			return nil, retries, 0
		}
		resp, err := client.Do(req)
		if err != nil {
			cancel()
			retries++
			if attempt == maxRetries {
				return nil, retries, lastStatus
			}
			time.Sleep(time.Duration(500*(attempt+1)) * time.Millisecond)
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		lastStatus = resp.StatusCode
		cancel()
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			return body, retries, lastStatus
		}
		retries++
		if attempt == maxRetries {
			return body, retries, lastStatus
		}
		time.Sleep(time.Duration(500*(attempt+1)) * time.Millisecond)
	}
	return nil, retries, lastStatus
}

func extractPubchem(body []byte) (smiles string, mw float64) {
	if len(body) == 0 {
		return "", 0
	}
	var parsed struct {
		PropertyTable struct {
			Properties []struct {
				CanonicalSMILES    string  `json:"CanonicalSMILES"`
				ConnectivitySMILES string  `json:"ConnectivitySMILES"`
				SMILES             string  `json:"SMILES"`
				MolecularWeight    string  `json:"MolecularWeight"`
				MolecularWeightF   float64 `json:"-"`
			} `json:"Properties"`
		} `json:"PropertyTable"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return "", 0
	}
	if len(parsed.PropertyTable.Properties) == 0 {
		return "", 0
	}
	p := parsed.PropertyTable.Properties[0]
	if p.CanonicalSMILES != "" {
		smiles = p.CanonicalSMILES
	} else if p.ConnectivitySMILES != "" {
		smiles = p.ConnectivitySMILES
	} else {
		smiles = p.SMILES
	}
	if p.MolecularWeight != "" {
		var f float64
		if _, err := fmt.Sscanf(p.MolecularWeight, "%f", &f); err == nil {
			mw = f
		}
	}
	return smiles, mw
}

func safeFilename(s string) string {
	var b strings.Builder
	for _, r := range s {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	return strings.ToLower(b.String())
}

func urlQuote(s string) string {
	// minimal percent-encoding for path segments; spaces and a few reserved.
	var b strings.Builder
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z',
			r >= 'A' && r <= 'Z',
			r >= '0' && r <= '9',
			r == '-' || r == '_' || r == '.' || r == '~':
			b.WriteRune(r)
		default:
			b.WriteString(fmt.Sprintf("%%%02X", r))
		}
	}
	return b.String()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
