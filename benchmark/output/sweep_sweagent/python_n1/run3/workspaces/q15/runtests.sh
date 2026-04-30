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

run_one "test_admin_locked_should_be_denied" 'from perm import can_access; assert not can_access(True, False, True)'
run_one "test_owner_unlocked" 'from perm import can_access; assert can_access(False, True, False)'
