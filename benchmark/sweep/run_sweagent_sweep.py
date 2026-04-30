"""Phase-2 SWE-Agent concurrency sweep wrapper.

Drives the existing per-language harnesses (`benchmark.run_sweagent_batch` for
Python, `cmd/sweagent` for Go) once per (lang, N) point, --runs 3.
Memory safety modeled on benchmark/sweep/run_full_sweep.py (ChemCrow Phase 2.5).
Mirrors benchmark/sweep/run_toolformer_sweep.py.

Sweep matrix
------------
  N ∈ {1, 2, 4, 8, 16}, langs ∈ {python, go}, runs = 3, queries = 20.
  Order: interleaved by N (py N=1, go N=1, py N=2, go N=2, ...) so a mid-sweep
  interrupt still leaves complete cross-lang coverage at every completed N.

OK-gate
-------
A point is OK iff:
  * num_failures / total < 30%, OR
  * 100% of failures match exactly "Control request timeout: initialize"

Per-point summary.json
----------------------
Schema parallels benchmark/output/sweep_toolformer/*/summary.json with
SWE-Agent-specific aggregates:
  - bash_spawn_ms  pooled stat block
  - bash_work_ms   pooled stat block
  - num_bash_calls per-query medians
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

REPO = Path(__file__).resolve().parents[2]
SWEEP_ROOT = REPO / "benchmark" / "output" / "sweep_sweagent"
LOGS_DIR = SWEEP_ROOT / "logs"
MASTER_LOG = SWEEP_ROOT / "full_sweep.log"
QUERIES = REPO / "benchmark" / "queries" / "sweagent_20.json"

# Sweep matrix reduced for tighter token budget after the original Phase 2 sweep
# hit the Pro-plan monthly cap mid-flight. N=16 dropped, --runs reduced from 3 to 2.
# Cells already complete with the old RUNS=3 (python_n1, go_n1, python_n2, go_n2)
# are detected via `len(existing_runs) >= RUNS` and left untouched.
NS = (1, 2, 4, 8)
LANGS = ("python", "go")
RUNS = 2
PER_POINT_TIMEOUT_S = 90 * 60   # SWE-Agent queries are heavier than Toolformer
RAM_GATE_MB = 1500
RAM_THRASH_MB = 800
SAMPLE_INTERVAL_S = 5

MCP_RACE_ERROR_TOKENS = (
    "Control request timeout: initialize",
)
QUERY_COUNT = 20


# ---------- master log + memory helpers ---------------------------------------

def _avail_mb() -> float:
    return psutil.virtual_memory().available / 1024**2


def _master(line: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamped = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}"
    print(stamped, flush=True)
    with MASTER_LOG.open("a") as f:
        f.write(stamped + "\n")


def _wait_for_ram() -> bool:
    avail = _avail_mb()
    if avail >= RAM_GATE_MB:
        return True
    _master(f"WARN  pre-point RAM {avail:.0f}MB < {RAM_GATE_MB}MB; sleeping 30s for recovery")
    time.sleep(30)
    avail = _avail_mb()
    if avail >= RAM_GATE_MB:
        _master(f"INFO  RAM recovered to {avail:.0f}MB; proceeding")
        return True
    _master(f"WARN  RAM still {avail:.0f}MB < {RAM_GATE_MB}MB after wait; skipping point")
    return False


def _sample_loop(proc: subprocess.Popen, peak: list[float],
                 thrash: list[bool], stop: threading.Event,
                 lang: str, n: int) -> None:
    while not stop.is_set():
        try:
            parent = psutil.Process(proc.pid)
            rss = parent.memory_info().rss
            for child in parent.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            peak[0] = max(peak[0], rss / 1024**2)
            avail = _avail_mb()
            if avail < RAM_THRASH_MB and not thrash[0]:
                thrash[0] = True
                _master(f"WARN  {lang} N={n}  avail RAM dropped to {avail:.0f}MB "
                        f"(< {RAM_THRASH_MB}MB swap-thrash threshold); data may be tainted")
        except psutil.NoSuchProcess:
            break
        except Exception as e:
            _master(f"WARN  sampler error in {lang} N={n}: {e!r}")
        if stop.wait(timeout=SAMPLE_INTERVAL_S):
            return


# ---------- harness drivers ---------------------------------------------------

def _python_cmd(out_dir: Path, n: int) -> list[str]:
    return [
        sys.executable, "-m", "benchmark.run_sweagent_batch",
        "--queries", str(QUERIES),
        "--out", str(out_dir),
        "--concurrency", str(n),
    ]


def _go_cmd(out_dir: Path, n: int) -> list[str]:
    rel_q = str(QUERIES.relative_to(REPO))
    rel_out = str(out_dir.relative_to(REPO))
    return [
        "go", "run", "./cmd/sweagent",
        "--queries", "../" + rel_q,
        "--out", "../" + rel_out,
        "--concurrency", str(n),
    ]


def _run_subprocess(lang: str, n: int, run_dir: Path, log_path: Path,
                    peak_out: list[float], thrash_out: list[bool]) -> tuple[int, float]:
    run_dir.mkdir(parents=True, exist_ok=True)
    if lang == "python":
        cmd = _python_cmd(run_dir, n)
        cwd = REPO
    else:
        cmd = _go_cmd(run_dir, n)
        cwd = REPO / "benchmark-go"
    t0 = time.monotonic()
    stop = threading.Event()
    with log_path.open("w") as logf:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT)
        sampler = threading.Thread(target=_sample_loop,
                                   args=(proc, peak_out, thrash_out, stop, lang, n),
                                   daemon=True)
        sampler.start()
        try:
            rc = proc.wait(timeout=PER_POINT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = -9
        stop.set()
        sampler.join(timeout=10)
    return rc, time.monotonic() - t0


# ---------- trace parsing -----------------------------------------------------

def _is_mcp_race_error(s: str | None) -> bool:
    if not s:
        return False
    return any(tok in s for tok in MCP_RACE_ERROR_TOKENS)


def _parse_run(run_dir: Path, queries: list[dict]) -> dict[str, Any]:
    """Read trace JSONs in run_dir and produce per-run metric lists + counts."""
    orch: list[float] = []
    active: list[float] = []
    gross: list[float] = []
    agent_cpu: list[float] = []
    bash_spawn: list[float] = []
    bash_work: list[float] = []
    n_bash_per_q: list[int] = []
    failures: int = 0
    mcp_race: int = 0
    for q in queries:
        p = run_dir / f"{q['query_id']}.json"
        if not p.exists():
            failures += 1
            continue
        try:
            t = json.loads(p.read_text())
        except Exception:
            failures += 1
            continue
        spans = sorted(t["spans"], key=lambda s: s["start_ns"])
        root = next((s for s in spans if s["parent_id"] is None), None)
        if root is None:
            failures += 1
            continue
        if root.get("status") == "error":
            failures += 1
            err = root.get("error") or root.get("attrs", {}).get("agent.error", "")
            if _is_mcp_race_error(err):
                mcp_race += 1
            continue
        gross.append(root["wall_time_ms"])
        agent_cpu.append(float(root["attrs"].get("agent.cpu_time_ms", 0.0)))
        child_sum = sum(s["wall_time_ms"] for s in spans if s.get("parent_id") == root["span_id"])
        orch.append(max(root["wall_time_ms"] - child_sum, 0.0))
        retry_wait = sum(s["wall_time_ms"] for s in spans if s["name"] == "llm.retry_wait")
        active.append(max(root["wall_time_ms"] - retry_wait, 0.0))
        n_bash = 0
        for s in spans:
            if s["name"] == "tool.bash_spawn":
                bash_spawn.append(s["wall_time_ms"])
            elif s["name"] == "tool.bash_work":
                bash_work.append(s["wall_time_ms"])
            elif s["name"] == "tool.bash_run":
                n_bash += 1
        n_bash_per_q.append(n_bash)
    return {
        "orchestration_ms": orch,
        "active_latency_ms": active,
        "gross_wallclock_ms": gross,
        "agent_cpu_time_ms": agent_cpu,
        "bash_spawn_ms": bash_spawn,
        "bash_work_ms": bash_work,
        "num_bash_calls": n_bash_per_q,
        "num_failures": failures,
        "num_mcp_race_failures": mcp_race,
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    if len(sv) == 1:
        return sv[0]
    pos = q * (len(sv) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sv) - 1)
    frac = pos - lo
    return sv[lo] * (1 - frac) + sv[hi] * frac


def _stat_block(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "mean": 0.0, "n": 0}
    return {
        "p50": float(statistics.median(values)),
        "p90": float(_quantile(values, 0.9)),
        "mean": float(statistics.fmean(values)),
        "n": len(values),
    }


# ---------- per-point orchestration ------------------------------------------

def _parse_wallclock_ms(log_path: Path) -> float:
    if not log_path.exists():
        return 0.0
    txt = log_path.read_text()
    m = re.search(r"WALLCLOCK_MS=([0-9.]+)", txt)
    if m:
        return float(m.group(1))
    m = re.search(r"wallclock=([0-9.]+)s", txt)
    if m:
        return float(m.group(1)) * 1000.0
    return 0.0


def _existing_valid_runs(point_dir: Path, queries: list[dict]) -> list[int]:
    """Indexes of run<N> dirs whose 20 traces all parse and are non-errored.

    Returns sorted list of integer indexes. Used by run_point to skip already-
    finished runs from a partial-resume.
    """
    valid: list[int] = []
    if not point_dir.exists():
        return valid
    for run_dir in sorted(point_dir.iterdir()):
        if not (run_dir.is_dir() and run_dir.name.startswith("run")):
            continue
        try:
            idx = int(run_dir.name[3:])
        except ValueError:
            continue
        ok = 0
        for q in queries:
            p = run_dir / f"{q['query_id']}.json"
            if not p.exists():
                continue
            try:
                t = json.loads(p.read_text())
            except Exception:
                continue
            root = next((s for s in t["spans"] if s["parent_id"] is None), None)
            if root is not None and root.get("status") != "error":
                ok += 1
        if ok == len(queries):
            valid.append(idx)
    return sorted(valid)


def _peak_from_master_log(lang: str, n: int, run_idx: int) -> float:
    """Recover peak_rss for an already-finished run from the master log.

    Each run line looks like:
        [ts]   run1/3 python N=4  rc=0  dt=156s wall=152.8s  ok=20/20  fail=0 (mcp_race=0)  peak_rss=1334MB
    Returns 0.0 if no match.
    """
    if not MASTER_LOG.exists():
        return 0.0
    pat = re.compile(
        rf"\brun{run_idx}/\d+\s+{re.escape(lang)}\s+N={n}\b.*?peak_rss=(\d+)MB"
    )
    for line in MASTER_LOG.read_text().splitlines():
        m = pat.search(line)
        if m:
            return float(m.group(1))
    return 0.0


def _build_run_blob(lang: str, n: int, r: int, run_dir: Path, queries: list[dict],
                    peak_mb: float, thrash: bool, rc: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse trace dir + harness log into a runs[] entry. Returns (blob, per)."""
    log_path = LOGS_DIR / f"{lang}_n{n}_run{r}.log"
    wall_ms = _parse_wallclock_ms(log_path)
    per = _parse_run(run_dir, queries)
    n_total = len(per["gross_wallclock_ms"]) + per["num_failures"]
    qps = (n_total / (wall_ms / 1000.0)) if wall_ms > 0 else 0.0
    blob = {
        "wallclock_ms": wall_ms,
        "throughput_qps": qps,
        "num_failures": per["num_failures"],
        "num_mcp_race_failures": per["num_mcp_race_failures"],
        "peak_memory_mb": peak_mb,
        "thrash_warning": thrash,
        "rc": rc,
        "orchestration_ms": per["orchestration_ms"],
        "active_latency_ms": per["active_latency_ms"],
        "gross_wallclock_ms": per["gross_wallclock_ms"],
        "agent_cpu_time_ms": per["agent_cpu_time_ms"],
        "bash_spawn_ms": per["bash_spawn_ms"],
        "bash_work_ms": per["bash_work_ms"],
        "num_bash_calls": per["num_bash_calls"],
    }
    return blob, per


def run_point(lang: str, n: int, queries: list[dict],
              resume: bool = True) -> tuple[str, dict[str, Any] | None]:
    point_dir = SWEEP_ROOT / f"{lang}_n{n}"
    summary_path = point_dir / "summary.json"
    # Skip if already complete with status OK and at least RUNS runs. The
    # `>=` (not `==`) is what lets cells from the original 3-run sweep
    # (python_n1/n2, go_n1/n2) skip cleanly under the new RUNS=2 budget.
    if resume and summary_path.exists():
        try:
            existing_summary = json.loads(summary_path.read_text())
            n_runs = len(existing_summary.get("runs", []))
            st = existing_summary.get("status", "")
            if n_runs >= RUNS and st in ("OK", "OK_WITH_MCP_RACE"):
                _master(f"SKIP-DONE {lang} N={n}  already complete "
                        f"({n_runs} runs on disk, status={st})")
                return st, existing_summary
        except Exception:
            pass
    if not _wait_for_ram():
        return "SKIPPED:low_ram", None
    point_dir.mkdir(parents=True, exist_ok=True)

    # Partial-resume: count run<r> dirs that have all 20 valid traces; keep
    # them as-is, run only the missing ones to reach RUNS total.
    existing = _existing_valid_runs(point_dir, queries)
    next_idx = (max(existing) if existing else 0) + 1
    n_new = max(0, RUNS - len(existing))
    new_indexes = list(range(next_idx, next_idx + n_new))
    avail0 = _avail_mb()
    _master(f"START {lang} N={n}  avail_ram={avail0:.0f}MB  "
            f"existing_runs={existing}  new_runs={new_indexes}  target={RUNS}")

    runs_blob: list[dict[str, Any]] = []
    pooled_orch: list[float] = []
    pooled_active: list[float] = []
    pooled_gross: list[float] = []
    pooled_cpu: list[float] = []
    pooled_spawn: list[float] = []
    pooled_work: list[float] = []
    per_query_bash_count_medians: list[float] = []
    peak_overall: list[float] = [0.0]
    thrash_overall: list[bool] = [False]
    point_t0 = time.monotonic()

    # Re-incorporate already-valid runs (no API cost; just parse + reuse).
    for r in existing:
        run_dir = point_dir / f"run{r}"
        prior_peak = _peak_from_master_log(lang, n, r)
        blob, per = _build_run_blob(lang, n, r, run_dir, queries,
                                     peak_mb=prior_peak, thrash=False, rc=0)
        runs_blob.append(blob)
        pooled_orch.extend(per["orchestration_ms"])
        pooled_active.extend(per["active_latency_ms"])
        pooled_gross.extend(per["gross_wallclock_ms"])
        pooled_cpu.extend(per["agent_cpu_time_ms"])
        pooled_spawn.extend(per["bash_spawn_ms"])
        pooled_work.extend(per["bash_work_ms"])
        if per["num_bash_calls"]:
            per_query_bash_count_medians.append(
                float(statistics.median(per["num_bash_calls"])))
        peak_overall[0] = max(peak_overall[0], prior_peak)
        _master(f"  run{r}/{RUNS} {lang} N={n}  REUSED  "
                f"ok={len(per['gross_wallclock_ms'])}/{QUERY_COUNT}  "
                f"peak_rss={prior_peak:.0f}MB (from master log)")

    # Execute only the new runs.
    for r in new_indexes:
        run_dir = point_dir / f"run{r}"
        log_path = LOGS_DIR / f"{lang}_n{n}_run{r}.log"
        peak: list[float] = [0.0]
        thrash: list[bool] = [False]
        rc, dt = _run_subprocess(lang, n, run_dir, log_path, peak, thrash)
        peak_overall[0] = max(peak_overall[0], peak[0])
        if thrash[0]:
            thrash_overall[0] = True
        blob, per = _build_run_blob(lang, n, r, run_dir, queries,
                                     peak_mb=peak[0], thrash=thrash[0], rc=rc)
        # Backfill wall_ms from dt if log was missing/malformed.
        if blob["wallclock_ms"] == 0.0:
            blob["wallclock_ms"] = dt * 1000.0
            n_total = len(per["gross_wallclock_ms"]) + per["num_failures"]
            blob["throughput_qps"] = n_total / dt if dt > 0 else 0.0
        runs_blob.append(blob)
        pooled_orch.extend(per["orchestration_ms"])
        pooled_active.extend(per["active_latency_ms"])
        pooled_gross.extend(per["gross_wallclock_ms"])
        pooled_cpu.extend(per["agent_cpu_time_ms"])
        pooled_spawn.extend(per["bash_spawn_ms"])
        pooled_work.extend(per["bash_work_ms"])
        if per["num_bash_calls"]:
            per_query_bash_count_medians.append(
                float(statistics.median(per["num_bash_calls"])))
        _master(f"  run{r}/{RUNS} {lang} N={n}  rc={rc}  dt={dt:.0f}s "
                f"wall={blob['wallclock_ms']/1000:.1f}s  "
                f"ok={len(per['gross_wallclock_ms'])}/{QUERY_COUNT}  "
                f"fail={per['num_failures']} (mcp_race={per['num_mcp_race_failures']})  "
                f"peak_rss={peak[0]:.0f}MB")

    point_dt = time.monotonic() - point_t0

    total_failures = sum(r["num_failures"] for r in runs_blob)
    total_mcp_race = sum(r["num_mcp_race_failures"] for r in runs_blob)
    total_attempts = QUERY_COUNT * len(runs_blob)
    fail_frac = total_failures / total_attempts
    if total_failures == 0:
        status = "OK"
    elif fail_frac < 0.30:
        status = "OK"
    elif total_mcp_race == total_failures:
        status = "OK_WITH_MCP_RACE"
    else:
        status = "FAIL"

    summary = {
        "lang": lang,
        "N": n,
        "status": status,
        "llm_transport": "claude-cli",
        "runs": runs_blob,
        "orchestration_ms": _stat_block(pooled_orch),
        "active_latency_ms": _stat_block(pooled_active),
        "gross_wallclock_ms": _stat_block(pooled_gross),
        "agent_cpu_time_ms": _stat_block(pooled_cpu),
        "bash_spawn_ms": _stat_block(pooled_spawn),
        "bash_work_ms": _stat_block(pooled_work),
        "num_bash_calls": {
            "p50": float(statistics.median(per_query_bash_count_medians))
                if per_query_bash_count_medians else 0.0,
            "mean": float(statistics.fmean(per_query_bash_count_medians))
                if per_query_bash_count_medians else 0.0,
        },
        "total_attempts": total_attempts,
        "total_failures": total_failures,
        "total_mcp_race_failures": total_mcp_race,
        "peak_memory_mb": peak_overall[0],
        "swap_thrash_warning": thrash_overall[0],
        "point_wallclock_s": point_dt,
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    p50_wall = summary["gross_wallclock_ms"]["p50"]
    p50_cpu = summary["agent_cpu_time_ms"]["p50"]
    _master(f"{status} {lang} N={n}  wallclock={point_dt:.0f}s  "
            f"p50={p50_wall:.0f}ms  agent_cpu={p50_cpu:.1f}ms  "
            f"failures={total_failures}/{total_attempts}  (mcp_race={total_mcp_race})  "
            f"peak_rss={peak_overall[0]:.0f}MB  thrash={thrash_overall[0]}")
    return status, summary


# ---------- main --------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-resume", action="store_true",
                        help="re-run all points even if summary.json already exists")
    parser.add_argument("--only-n", type=int, default=None)
    parser.add_argument("--only-lang", choices=("python", "go"), default=None)
    args = parser.parse_args()

    SWEEP_ROOT.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    queries = json.loads(QUERIES.read_text())["queries"]
    if not queries or len(queries) != QUERY_COUNT:
        _master(f"ERROR queries file has {len(queries)} queries, expected {QUERY_COUNT}")
        return 2

    points: list[tuple[str, int]] = []
    for n in NS:
        if args.only_n is not None and n != args.only_n:
            continue
        for lang in LANGS:
            if args.only_lang is not None and lang != args.only_lang:
                continue
            points.append((lang, n))

    _master(f"BEGIN sweagent sweep: {len(points)} points, runs={RUNS}, queries={QUERY_COUNT}, "
            f"avail_ram={_avail_mb():.0f}MB")
    sweep_t0 = time.monotonic()
    rows: list[tuple[str, int, str, dict | None]] = []
    for lang, n in points:
        status, sm = run_point(lang, n, queries, resume=not args.no_resume)
        rows.append((lang, n, status, sm))
    total = time.monotonic() - sweep_t0
    _master(f"END   sweagent sweep total_wallclock={total:.0f}s ({total/3600:.2f}h)")

    print()
    print("=" * 120)
    print(f"{'lang':<6}  {'N':>3}  {'status':<18}  {'wall_p50':>9}  {'cpu_p50':>9}  "
          f"{'spawn_p50':>10}  {'work_p50':>9}  {'fail':>9}  {'mcp_race':>9}  {'peak_MB':>8}")
    print("-" * 120)
    for lang, n, status, sm in rows:
        if sm is None:
            print(f"{lang:<6}  {n:>3}  {status:<18}  {'-':>9}  {'-':>9}  {'-':>10}  "
                  f"{'-':>9}  {'-':>9}  {'-':>9}  {'-':>8}")
            continue
        print(f"{lang:<6}  {n:>3}  {status:<18}  "
              f"{sm['gross_wallclock_ms']['p50']:>9.0f}  "
              f"{sm['agent_cpu_time_ms']['p50']:>9.2f}  "
              f"{sm['bash_spawn_ms']['p50']:>10.2f}  "
              f"{sm['bash_work_ms']['p50']:>9.2f}  "
              f"{sm['total_failures']:>4}/{sm['total_attempts']:<4}  "
              f"{sm['total_mcp_race_failures']:>9}  "
              f"{sm['peak_memory_mb']:>8.0f}")
    print("=" * 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
