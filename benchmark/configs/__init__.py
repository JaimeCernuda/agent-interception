"""Benchmark configurations.

Py: Python tool runtime (this directory).
Go: Go tool runtime (benchmark-go/, runs as a separate binary).

The Python CLI (run.py) only dispatches Py. The Go CLI lives in benchmark-go.
Both produce JSON traces with identical span schema consumed by analysis/.
"""
from benchmark.configs import config_py

CONFIGS = {
    "py": config_py,
}
