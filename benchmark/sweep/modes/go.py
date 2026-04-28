"""Go harness: subprocess into the Go chemcrow binary.

The Go binary's own `--concurrency` flag enforces the N-bound (sync.WaitGroup
+ bounded channel). We just shell out, parse the WALLCLOCK_MS line it prints,
and read the resulting trace JSONs.

The binary is pre-built once on first call (cached in benchmark-go/.bin/) so
cold-start measurements reflect Go RUNTIME startup, not Go compilation. With
`go run` cold_start_ms swelled to ~190s on a clean cache; the prebuilt binary
keeps it in the few-hundred-ms range, matching what a deployed Go agent looks like.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from benchmark.sweep.summary import BatchResult

# Module-relative repo root: benchmark/sweep/modes/go.py -> repo
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCHMARK_GO_DIR = _REPO_ROOT / "benchmark-go"
_BIN_DIR = _BENCHMARK_GO_DIR / ".bin"
_BIN_PATH = _BIN_DIR / "chemcrow"

_WALLCLOCK_RE = re.compile(r"^WALLCLOCK_MS=([0-9.]+)\s*$", re.M)
_BUILD_LOCK = threading.Lock()


def _ensure_binary() -> Path:
    """Build benchmark-go/cmd/chemcrow once; cache under .bin/.

    Source change → re-build by `rm -rf benchmark-go/.bin/`. We deliberately
    don't stat-mtime the sources here — the sweep is the place we want stable
    binaries, not auto-rebuilds.
    """
    with _BUILD_LOCK:
        if _BIN_PATH.exists():
            return _BIN_PATH
        _BIN_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["go", "build", "-o", str(_BIN_PATH), "./cmd/chemcrow"],
            cwd=_BENCHMARK_GO_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        return _BIN_PATH


def run_batch(queries: list[dict], out_dir: Path, concurrency: int, *,
              config_name: str = "chemcrow_go",
              queries_path: Path | None = None) -> BatchResult:
    out_dir.mkdir(parents=True, exist_ok=True)

    # The Go binary reads a queries FILE, not a list. To run a subset (cold
    # start = 1 query, warmup = 4 queries), write a temp queries file with
    # only the requested entries. Using the original queries_path here would
    # silently make the binary process all 20.
    tmp_queries = Path(tempfile.mkstemp(prefix="chemcrow_subset_", suffix=".json")[1])
    tmp_queries.write_text(json.dumps({"queries": queries}))

    try:
        bin_path = _ensure_binary()
        out_abs = out_dir.resolve()
        queries_abs = tmp_queries.resolve()

        cmd = [
            str(bin_path),
            "--queries", str(queries_abs),
            "--out", str(out_abs),
            "--config", config_name,
            "--concurrency", str(concurrency),
            "--env", str((_REPO_ROOT / "benchmark" / ".env").resolve()),
            # Phase-2.5: route Go through Pro-plan tokens via the Python config.
            # Removes the metered-API dependency that bricked the first sweep
            # attempt. Each Go goroutine spawns a Python subprocess that does
            # the LLM call + tool dispatch via claude-agent-sdk, exactly like
            # py-mp's Python harness does.
            "--use-pro-plan",
            "--python", str(_REPO_ROOT / ".venv" / "bin" / "python"),
        ]
        env = {**os.environ}
        # Make sure the Go binary can find a Python with rdkit installed for
        # smiles_to_3d / compute_descriptors.
        env.setdefault("CHEMCROW_PYTHON", str(_REPO_ROOT / ".venv" / "bin" / "python"))

        t0 = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=_BENCHMARK_GO_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        py_wall_ms = (time.monotonic() - t0) * 1000.0

        # Prefer the Go binary's wall measurement (matches what the agent saw);
        # fall back to the Python-side measurement if parsing fails.
        m = _WALLCLOCK_RE.search(proc.stdout or "")
        wall_ms = float(m.group(1)) if m else py_wall_ms

        failures = 0
        if proc.returncode != 0:
            # Even on non-zero exit, individual queries may have succeeded — count
            # FAIL lines in stdout rather than mark the whole batch failed.
            failures = sum(1 for ln in (proc.stdout or "").splitlines() if " FAIL " in ln)
            if failures == 0:
                failures = len(queries)

        trace_paths = [out_dir / f"{q['query_id']}.json" for q in queries]
        trace_paths = [p for p in trace_paths if p.exists()]
        extra = {
            "go_stdout_tail": (proc.stdout or "")[-2000:],
            "go_stderr_tail": (proc.stderr or "")[-2000:],
            "go_returncode": proc.returncode,
        }
        return BatchResult(
            wallclock_ms=wall_ms,
            trace_paths=trace_paths,
            num_failures=failures,
            extra=extra,
        )
    finally:
        try:
            tmp_queries.unlink()
        except OSError:
            pass
