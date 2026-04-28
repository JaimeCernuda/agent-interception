# pyright: reportMissingImports=false
"""Toolformer tools (Python side).

One instrumented tool: `calculator`. Evaluates an arithmetic expression with
simpleeval (no `eval()`, no `exec()`) and a curated set of math functions.

Span vocabulary matches the Go side (benchmark-go/internal/toolformer/) so the
cross-language schema test stays green:

  - tool.calculator   (one span per call, no decomposition — evaluation is microseconds)

Span attrs:
  - tool.name         "calculator"
  - tool.input_hash   16-hex sha256 of the expression
  - expression        first 100 chars of the expression
  - result            float result on success (omitted on error)
  - error             error message on failure (omitted on success)

Returns: {"result": float | None, "error": str | None}. The agent can retry
with a corrected expression if `error` is non-null.
"""
from __future__ import annotations

import math

from benchmark.obs import Observer, input_hash

try:
    from simpleeval import SimpleEval
except ImportError:  # pragma: no cover - exercised on broken installs
    SimpleEval = None  # type: ignore[assignment]


_PREVIEW_CHARS = 100

# Math functions exposed to the calculator. Same set as the Go side.
_FUNCTIONS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "min": min,
    "max": max,
    "pow": pow,
}

_NAMES = {
    "pi": math.pi,
    "e": math.e,
}


def calculator(expression: str, obs: Observer) -> dict:
    """Evaluate `expression` and emit one tool.calculator span.

    Returns {"result": float | None, "error": str | None}. Never raises.
    """
    preview = expression[:_PREVIEW_CHARS]
    with obs.span(
        "tool.calculator",
        **{
            "tool.name": "calculator",
            "tool.input_hash": input_hash(expression),
            "expression": preview,
        },
    ) as span:
        if SimpleEval is None:
            err = "simpleeval not installed"
            span.set("error", err)
            return {"result": None, "error": err}

        evaluator = SimpleEval(functions=dict(_FUNCTIONS), names=dict(_NAMES))
        try:
            value = evaluator.eval(expression)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            span.set("error", err)
            return {"result": None, "error": err}

        try:
            result_f = float(value)
        except (TypeError, ValueError) as e:
            err = f"non-numeric result: {type(e).__name__}: {e}"
            span.set("error", err)
            return {"result": None, "error": err}

        span.set("result", result_f)
        return {"result": result_f, "error": None}


__all__ = ["calculator"]
