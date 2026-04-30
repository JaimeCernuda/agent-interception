#!/usr/bin/env bash
# Custom test runner — not pytest. Output format the agent must parse:
#   TEST: <name> PASS|FAIL
#   TRACE: <file>:<line> <ExceptionType>: <message>
set -u
cd "$(dirname "$0")"

run_one() {
    local name="$1"
    local script="$2"
    local out
    if out=$(python3 -c "$script" 2>&1); then
        echo "TEST: $name PASS"
    else
        echo "TEST: $name FAIL"
        # Minimal traceback line.
        echo "$out" | tail -n 3 | sed -e 's/^/TRACE: /'
    fi
}

run_one "test_first_n_three" "from slicer import first_n; assert first_n([1,2,3,4,5], 3) == [1,2,3], 'expected [1,2,3]'"
run_one "test_first_n_zero" "from slicer import first_n; assert first_n([1,2,3], 0) == []"
