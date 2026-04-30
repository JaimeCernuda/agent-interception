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

run_one "test_square_describe" "from describe import describe; from shapes import SQUARE; assert describe(SQUARE) == '4 sides'"
run_one "test_triangle_describe" "from describe import describe; from shapes import TRIANGLE; assert describe(TRIANGLE) == '3 sides'"
