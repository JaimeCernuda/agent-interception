"""Phase-2.5 full sweep wrapper. 14 (mode, N) points, --runs 3, sequential.

Drives benchmark/sweep/runner.py one point at a time. Per-point stdout/stderr
goes to benchmark/output/sweep/logs/<mode>_n<N>.log. Master status line per
point goes to benchmark/output/sweep/full_sweep.log.

Memory safety (this machine has 8 GB total):
  - Sweep range pruned to N ∈ {1,2,4,8,16} for py-mt/go and {1,2,4,8} for py-mp.
  - Pre-point gate: if available RAM < 1500 MB, wait 30 s and retry; skip on
    second miss.
  - During-point sampler: psutil thread every 5 s tracks peak RSS of the
    runner subprocess + recursive children, and watches for available RAM
    below 800 MB (swap-thrash zone).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[2]
SWEEP_ROOT = REPO / "benchmark" / "output" / "sweep"
LOGS_DIR = SWEEP_ROOT / "logs"
MASTER_LOG = SWEEP_ROOT / "full_sweep.log"
QUERIES = REPO / "benchmark" / "queries" / "chemcrow_20.json"

NS_PER_MODE = {
    "py-mt": (1, 2, 4, 8, 16),
    "py-mp": (1, 2, 4, 8),
    "go":    (1, 2, 4, 8, 16),
}
PER_POINT_TIMEOUT_S = 60 * 60
RUNS = 3
RAM_GATE_MB = 1500
RAM_THRASH_MB = 800
SAMPLE_INTERVAL_S = 5


def _avail_mb() -> float:
    return psutil.virtual_memory().available / 1024**2


def _master(line: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamped = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}"
    print(stamped, flush=True)
    with MASTER_LOG.open("a") as f:
        f.write(stamped + "\n")


def _wait_for_ram() -> bool:
    """Pre-point gate. True iff RAM is healthy (>= RAM_GATE_MB), with one retry."""
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
                 mode: str, n: int) -> None:
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
                _master(f"WARN  {mode} N={n}  avail RAM dropped to {avail:.0f}MB "
                        f"(< {RAM_THRASH_MB}MB swap-thrash threshold); data may be tainted")
        except psutil.NoSuchProcess:
            break
        except Exception as e:  # don't kill the sampler over a transient psutil error
            _master(f"WARN  sampler error in {mode} N={n}: {e!r}")
        if stop.wait(timeout=SAMPLE_INTERVAL_S):
            return


def _read_summary(mode: str, n: int) -> dict | None:
    p = SWEEP_ROOT / f"{mode}_n{n}" / "summary.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _patch_summary_with_memory(mode: str, n: int, peak_mb: float, thrash: bool) -> None:
    p = SWEEP_ROOT / f"{mode}_n{n}" / "summary.json"
    if not p.exists():
        return
    try:
        sm = json.loads(p.read_text())
    except json.JSONDecodeError:
        return
    sm["peak_memory_mb"] = peak_mb
    sm["swap_thrash_warning"] = thrash
    for r in sm.get("runs", []):
        r["peak_memory_mb"] = peak_mb
    p.write_text(json.dumps(sm, indent=2))


def run_point(mode: str, n: int) -> str:
    # Resume support: skip a point that already has a complete summary.json
    # with `runs == RUNS`. The re-aggregator in run_full_sweep_resume.py
    # populates summary.json correctly for in-place updates after a metric fix.
    sm = _read_summary(mode, n)
    if sm is not None and len(sm.get("runs", [])) == RUNS:
        p50 = sm["orchestration_ms"]["p50"]
        p90 = sm["orchestration_ms"]["p90"]
        qps = sum(r.get("throughput_qps", 0) for r in sm["runs"]) / RUNS
        _master(f"SKIP-DONE {mode} N={n}  p50={p50:.0f}ms p90={p90:.0f}ms qps={qps:.3f} (already complete)")
        return "OK"
    if not _wait_for_ram():
        return "SKIPPED:low_ram"
    log_path = LOGS_DIR / f"{mode}_n{n}.log"
    cmd = [
        sys.executable, "-m", "benchmark.sweep.runner",
        "--mode", mode, "--concurrency", str(n),
        "--runs", str(RUNS), "--warmup-queries", "4",
        "--queries", str(QUERIES), "--out-root", str(SWEEP_ROOT),
        "--reset",
    ]
    avail0 = _avail_mb()
    _master(f"START {mode} N={n}  avail_ram={avail0:.0f}MB  log={log_path.relative_to(REPO)}")
    t0 = time.monotonic()
    peak: list[float] = [0.0]
    thrash: list[bool] = [False]
    stop = threading.Event()
    with log_path.open("w") as logf:
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=logf, stderr=subprocess.STDOUT)
        sampler = threading.Thread(target=_sample_loop,
                                   args=(proc, peak, thrash, stop, mode, n),
                                   daemon=True)
        sampler.start()
        try:
            rc = proc.wait(timeout=PER_POINT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            stop.set()
            sampler.join(timeout=10)
            _master(f"TIMEOUT {mode} N={n} after {PER_POINT_TIMEOUT_S}s peak_rss={peak[0]:.0f}MB")
            return "TIMEOUT"
        stop.set()
        sampler.join(timeout=10)
    dt = time.monotonic() - t0

    _patch_summary_with_memory(mode, n, peak[0], thrash[0])
    sm = _read_summary(mode, n)
    if rc != 0 or sm is None or len(sm.get("runs", [])) != RUNS:
        _master(f"FAIL  {mode} N={n}  rc={rc}  dt={dt:.0f}s  peak_rss={peak[0]:.0f}MB  "
                f"runs={len(sm.get('runs', [])) if sm else 0}/{RUNS}  thrash={thrash[0]}")
        return "FAILED"
    # Defense in depth: the runner exits rc=0 even when 100% of queries fail
    # (its contract treats failures as data). Don't mark a point OK if every
    # measured run was a wipeout.
    n_queries = max(1, len(sm["runs"][0].get("gross_wallclock_ms", [])))
    fail_fraction = sum(r.get("num_failures", 0) for r in sm["runs"]) / (RUNS * n_queries)
    if fail_fraction >= 0.5:
        _master(f"FAIL  {mode} N={n}  fail_fraction={fail_fraction:.0%}  rc={rc}  "
                f"dt={dt:.0f}s  peak_rss={peak[0]:.0f}MB  thrash={thrash[0]}  "
                f"(>=50% query failures across all runs)")
        return "FAILED"
    p50 = sm["orchestration_ms"]["p50"]
    p90 = sm["orchestration_ms"]["p90"]
    qps = sum(r["throughput_qps"] for r in sm["runs"]) / RUNS
    _master(f"OK    {mode} N={n}  dt={dt:.0f}s  peak_rss={peak[0]:.0f}MB  thrash={thrash[0]}  "
            f"p50={p50:.0f}ms p90={p90:.0f}ms qps={qps:.3f}")
    return "OK"


def main() -> int:
    SWEEP_ROOT.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if MASTER_LOG.exists():
        shutil.move(MASTER_LOG, MASTER_LOG.with_suffix(".log.prev"))
    points: list[tuple[str, int]] = []
    for n in (1, 2, 4, 8, 16):
        for mode in ("py-mt", "py-mp", "go"):
            if n in NS_PER_MODE[mode]:
                points.append((mode, n))
    _master(f"BEGIN full sweep: 14-point pruned plan, runs={RUNS} "
            f"avail_ram={_avail_mb():.0f}MB")
    sweep_t0 = time.monotonic()
    rows: list[tuple[str, int, str]] = []
    for mode, n in points:
        rows.append((mode, n, run_point(mode, n)))
    total_dt = time.monotonic() - sweep_t0
    _master(f"END   full sweep total_wallclock={total_dt:.0f}s ({total_dt/3600:.2f}h)")

    print("\n=== Final summary ===")
    print(f"{'mode':6s} {'N':>3s}  {'status':12s} {'p50_orch':>10s} {'p90_orch':>10s} "
          f"{'qps':>7s} {'peak_rss_MB':>12s} {'thrash':>7s}")
    for mode, n, status in rows:
        sm = _read_summary(mode, n)
        if status != "OK" or sm is None:
            print(f"{mode:6s} {n:>3d}  {status:12s} {'-':>10s} {'-':>10s} {'-':>7s} {'-':>12s} {'-':>7s}")
            continue
        p50 = sm["orchestration_ms"]["p50"]
        p90 = sm["orchestration_ms"]["p90"]
        qps = sum(r["throughput_qps"] for r in sm["runs"]) / RUNS
        peak = sm.get("peak_memory_mb", 0.0)
        thrash = "Y" if sm.get("swap_thrash_warning") else ""
        print(f"{mode:6s} {n:>3d}  {'OK':12s} {p50:>10.0f} {p90:>10.0f} {qps:>7.3f} "
              f"{peak:>12.0f} {thrash:>7s}")
    n_attempted = len(rows)
    n_ok = sum(1 for _, _, s in rows if s == "OK")
    print(f"\n{n_ok}/{n_attempted} successful, total wallclock {total_dt/3600:.2f}h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
