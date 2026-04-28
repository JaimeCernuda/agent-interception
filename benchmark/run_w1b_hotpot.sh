#!/usr/bin/env bash
# W1b extension — re-run the Python concurrent sweep on HotpotQA-20.
#
# 5 cells: b=[1,4,16,64] DDG primary + b=64 static validation.
# Go is skipped (DDG anti-scraping collapses Go's stdlib scraper at b>=4;
# the question here is about Python's GIL on a heavier summarize workload,
# not the cross-language race).
#
# Master log: benchmark/results/w1b_hotpot_master.log
# Output dirs: benchmark/results/cell_concurrent_py_hotpot_b{N}[_static]/

set -uo pipefail
cd "$(dirname "$0")/.."

LOG=benchmark/results/w1b_hotpot_master.log
mkdir -p benchmark/results
: > "$LOG"

run_py() {
    local batch=$1
    local backend=$2
    local label_suffix=$3
    local cell_name="cell_concurrent_py_hotpot_b${batch}${label_suffix}"
    local out_dir="benchmark/results/${cell_name}"
    rm -rf "$out_dir"
    {
        echo
        echo "=================================================================="
        echo "[CELL] py-hotpot  batch=$batch  backend=$backend  out=$out_dir"
        echo "[CELL] start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "=================================================================="
        SEARCH_BACKEND=$backend CONCURRENT_BATCH_SIZE=$batch \
            uv run --group benchmark python -m benchmark.configs.config_concurrent_py \
            --queries benchmark/queries/hotpotqa_20.json \
            --cell-name "$cell_name" \
            --out "$out_dir" 2>&1
        echo "[CELL] end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } | tee -a "$LOG"
}

cooldown() {
    echo "[sweep] cooling down 10s..." | tee -a "$LOG"
    sleep 10
}

# 4 primary cells: DDG live
run_py 1  ddg ""
cooldown
run_py 4  ddg ""
cooldown
run_py 16 ddg ""
cooldown
run_py 64 ddg ""
cooldown

# 1 validation cell: static at b=64
run_py 64 static "_static"

echo | tee -a "$LOG"
echo "[sweep] all 5 cells done at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
