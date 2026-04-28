// Package toolformer holds the Go-side Toolformer tool implementation and the
// CLI-driven agent loop. Span vocabulary (tool.calculator, attrs expression /
// result / error) matches the Python config so cross-language tests stay green.
package toolformer

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"

	"github.com/Knetic/govaluate"
	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const previewChars = 100

// calculatorFunctions is the curated math function set exposed inside the
// expression evaluator. Same names + semantics as benchmark/tools/toolformer.py.
var calculatorFunctions = map[string]govaluate.ExpressionFunction{
	"sqrt": floatUnary(math.Sqrt),
	"log":  floatUnary(math.Log),
	"exp":  floatUnary(math.Exp),
	"sin":  floatUnary(math.Sin),
	"cos":  floatUnary(math.Cos),
	"tan":  floatUnary(math.Tan),
	"abs":  floatUnary(math.Abs),
	"min": func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, fmt.Errorf("min: needs >=1 arg")
		}
		m, err := toFloat(args[0])
		if err != nil {
			return nil, err
		}
		for _, a := range args[1:] {
			v, err := toFloat(a)
			if err != nil {
				return nil, err
			}
			if v < m {
				m = v
			}
		}
		return m, nil
	},
	"max": func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, fmt.Errorf("max: needs >=1 arg")
		}
		m, err := toFloat(args[0])
		if err != nil {
			return nil, err
		}
		for _, a := range args[1:] {
			v, err := toFloat(a)
			if err != nil {
				return nil, err
			}
			if v > m {
				m = v
			}
		}
		return m, nil
	},
	"pow": func(args ...any) (any, error) {
		if len(args) != 2 {
			return nil, fmt.Errorf("pow: needs 2 args, got %d", len(args))
		}
		base, err := toFloat(args[0])
		if err != nil {
			return nil, err
		}
		exp, err := toFloat(args[1])
		if err != nil {
			return nil, err
		}
		return math.Pow(base, exp), nil
	},
}

// calculatorParams holds the constants pi and e (matches the Python side's
// SimpleEval names dict).
var calculatorParams = map[string]any{
	"pi": math.Pi,
	"e":  math.E,
}

func floatUnary(fn func(float64) float64) govaluate.ExpressionFunction {
	return func(args ...any) (any, error) {
		if len(args) != 1 {
			return nil, fmt.Errorf("expected 1 arg, got %d", len(args))
		}
		v, err := toFloat(args[0])
		if err != nil {
			return nil, err
		}
		return fn(v), nil
	}
}

func toFloat(v any) (float64, error) {
	switch n := v.(type) {
	case float64:
		return n, nil
	case float32:
		return float64(n), nil
	case int:
		return float64(n), nil
	case int64:
		return float64(n), nil
	default:
		return 0, fmt.Errorf("not a number: %v", v)
	}
}

// CalculatorResult is the JSON shape returned to the LLM by the calculator
// tool. Mirrors the Python side: {"result": float|null, "error": string|null}.
type CalculatorResult struct {
	Result *float64 `json:"result"`
	Error  *string  `json:"error"`
}

// Calculator evaluates `expression` and emits one tool.calculator span on `o`.
// Never panics; on any failure (parse error, divide-by-zero, non-numeric
// result) it returns {result=nil, error=<msg>} and tags the span with `error`.
func Calculator(expression string, o *obs.Observer) CalculatorResult {
	preview := expression
	if len(preview) > previewChars {
		preview = preview[:previewChars]
	}
	s := o.Start("tool.calculator", map[string]any{
		"tool.name":       "calculator",
		"tool.input_hash": inputHash(expression),
		"expression":      preview,
	})
	defer s.End()

	expr, err := govaluate.NewEvaluableExpressionWithFunctions(expression, calculatorFunctions)
	if err != nil {
		msg := "ParseError: " + err.Error()
		s.Set("error", msg)
		return CalculatorResult{Result: nil, Error: &msg}
	}
	val, err := expr.Evaluate(calculatorParams)
	if err != nil {
		msg := "EvalError: " + err.Error()
		s.Set("error", msg)
		return CalculatorResult{Result: nil, Error: &msg}
	}
	f, err := toFloat(val)
	if err != nil {
		msg := "non-numeric result: " + err.Error()
		s.Set("error", msg)
		return CalculatorResult{Result: nil, Error: &msg}
	}
	if math.IsNaN(f) || math.IsInf(f, 0) {
		msg := fmt.Sprintf("non-finite result: %v", f)
		s.Set("error", msg)
		return CalculatorResult{Result: nil, Error: &msg}
	}
	s.Set("result", f)
	return CalculatorResult{Result: &f, Error: nil}
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

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
