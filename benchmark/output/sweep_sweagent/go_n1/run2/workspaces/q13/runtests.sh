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

run_one "test_positive_int" 'from counter import is_positive; assert is_positive(5)'
run_one "test_zero_int" 'from counter import is_positive; assert not is_positive(0)'
