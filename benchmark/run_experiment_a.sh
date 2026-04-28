#!/usr/bin/env bash
# Experiment A — concurrent batch sweep across Py and Go.
#
# 10 cells total:
#   - 8 primary cells: Py+Go x b1,4,16,64 with SEARCH_BACKEND=ddg (live)
#   - 2 validation cells: Py+Go at b=64 with SEARCH_BACKEND=static
#
# Sleeps 10s between cells (DDG + Anthropic rate-limit cooldown).
# Master log: benchmark/results/experiment_a_master.log
#
# Designed to be safely re-runnable: each cell writes its own output dir,
# overwriting any previous run of the same (lang, batch, backend) tuple.
#
# IMPORTANT: SEARCH_BACKEND=ddg is exported explicitly because Go's auto
# resolves to "static" while Python's auto resolves to "ddg" (Python's
# default was deliberately flipped; Go was never updated). Setting it
# explicitly keeps the cross-language workload identical.

set -uo pipefail

cd "$(dirname "$0")/.."

LOG=benchmark/results/experiment_a_master.log
mkdir -p benchmark/results
: > "$LOG"

# --- Cleanup smoke test dirs from Phases 2 and 3 ---
rm -rf benchmark/results/cell_concurrent_py_b4_smoke
rm -rf benchmark/results/cell_concurrent_go_b4_smoke

run_py() {
    local batch=$1
    local backend=$2
    local label_suffix=$3
    local out_dir="benchmark/results/cell_concurrent_py_b${batch}${label_suffix}"
    rm -rf "$out_dir"  # keep cell self-contained
    {
        echo
        echo "=================================================================="
        echo "[CELL] py  batch=$batch  backend=$backend  out=$out_dir"
        echo "[CELL] start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "=================================================================="
        SEARCH_BACKEND=$backend CONCURRENT_BATCH_SIZE=$batch \
            uv run --group benchmark python -m benchmark.configs.config_concurrent_py \
            --queries benchmark/queries/freshqa_20.json \
            --out "$out_dir" 2>&1
        echo "[CELL] end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } | tee -a "$LOG"
}

run_go() {
    local batch=$1
    local backend=$2
    local label_suffix=$3
    local out_dir="benchmark/results/cell_concurrent_go_b${batch}${label_suffix}"
    rm -rf "$out_dir"
    {
        echo
        echo "=================================================================="
        echo "[CELL] go  batch=$batch  backend=$backend  out=$out_dir"
        echo "[CELL] start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "=================================================================="
        # benchmark-go expects the queries path relative to its own cwd,
        # since it loads ../benchmark/.env. We cd into it for the run.
        ( cd benchmark-go && \
          SEARCH_BACKEND=$backend CONCURRENT_BATCH_SIZE=$batch \
            go run ./cmd/concurrent_go \
            --queries ../benchmark/queries/freshqa_20.json \
            --out "../$out_dir" 2>&1 )
        echo "[CELL] end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } | tee -a "$LOG"
}

cooldown() {
    echo "[sweep] cooling down 10s..." | tee -a "$LOG"
    sleep 10
}

# --- 8 PRIMARY CELLS: DDG live ---
run_py 1  ddg ""
cooldown
run_go 1  ddg ""
cooldown
run_py 4  ddg ""
cooldown
run_go 4  ddg ""
cooldown
run_py 16 ddg ""
cooldown
run_go 16 ddg ""
cooldown
run_py 64 ddg ""
cooldown
run_go 64 ddg ""
cooldown

# --- 2 VALIDATION CELLS: static at b=64 ---
run_py 64 static "_static"
cooldown
run_go 64 static "_static"

echo | tee -a "$LOG"
echo "[sweep] all 10 cells done at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
