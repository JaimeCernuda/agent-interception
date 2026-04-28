"""Generate the 20 SWE-Agent workspace fixtures deterministically.

Runs:
    python benchmark/queries/generate_sweagent_workspaces.py

Outputs:
    benchmark/queries/sweagent_workspaces/q01..q20/

Idempotent: every workspace directory is wiped and rebuilt on each run, so a
second invocation produces no diff under git.

Categories:
    A (q01-q05): Large structured-log files. The agent uses bash text-tools
        (grep/awk/cut/sort/uniq) to extract candidate data, then aggregates
        in-process. Each file is 3-8 MB.
    B (q06-q10): Multi-file Python projects (8-15 files each) where the
        answer requires accumulating state across many files.
    C (q11-q15): Single-bug modules + a custom runtests.sh script that prints
        parseable TEST: PASS/FAIL lines.
    D (q16-q20): Aggregate-analysis tasks across 10-20 small files of varied
        formats.

Determinism: every per-query random.Random is seeded with (42 + query_index)
so workspaces are independent and reproducible.
"""
from __future__ import annotations

import json
import random
import shutil
import string
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACES = ROOT / "sweagent_workspaces"
SEED_BASE = 42

# ---- Category A: large logs ----------------------------------------------------

_HTTP_STATUSES = [200, 200, 200, 200, 200, 200, 304, 304, 404, 500]
_HTTP_PATHS = [
    "/", "/api/users", "/api/users/{id}", "/api/items", "/api/items/{id}",
    "/api/search", "/static/app.js", "/static/app.css", "/health", "/login",
    "/logout", "/api/orders", "/api/orders/{id}", "/admin", "/metrics",
]
_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605",
    "curl/7.79.1",
    "Python-urllib/3.11",
    "Go-http-client/1.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/118.0",
]


def _rand_ip(rng: random.Random) -> str:
    return f"{rng.randint(10, 250)}.{rng.randint(0, 250)}.{rng.randint(0, 250)}.{rng.randint(1, 254)}"


def _gen_q01_access_log(workspace: Path, rng: random.Random) -> None:
    """nginx-style access log, ~12 MB / 110k lines."""
    lines: list[str] = []
    epoch = 1_710_000_000
    for i in range(110_000):
        ts = epoch + i * 2 + rng.randint(0, 5)
        # CLF-ish line.
        ip = _rand_ip(rng)
        status = rng.choice(_HTTP_STATUSES)
        path = rng.choice(_HTTP_PATHS).replace("{id}", str(rng.randint(1, 99_999)))
        size = rng.randint(120, 18_000) if status != 304 else 0
        ua = rng.choice(_USER_AGENTS)
        lines.append(
            f'{ip} - - [{ts}] "GET {path} HTTP/1.1" {status} {size} "-" "{ua}"'
        )
    (workspace / "access.log").write_text("\n".join(lines) + "\n")


def _gen_q02_events(workspace: Path, rng: random.Random) -> None:
    """JSONL events, ~10 MB / 40k events."""
    types = [
        "user_login", "user_logout", "page_view", "click",
        "api_call", "api_error", "cache_hit", "cache_miss",
        "queue_enqueue", "queue_dequeue", "job_start", "job_complete",
        "purchase", "refund", "session_start", "session_end",
    ]
    epoch = 1_710_000_000
    out = []
    for i in range(40_000):
        ev = {
            "ts": epoch + i,
            "type": rng.choice(types),
            "user_id": rng.randint(1, 5000),
            "session_id": "".join(rng.choices(string.hexdigits.lower(), k=24)),
            "request_id": "".join(rng.choices(string.hexdigits.lower(), k=32)),
            "payload": {
                "client": rng.choice(["web", "ios", "android", "api"]),
                "region": rng.choice(["us-east", "us-west", "eu-west", "ap-south"]),
                "version": f"{rng.randint(1, 9)}.{rng.randint(0, 30)}.{rng.randint(0, 99)}",
                "lat": round(rng.uniform(-90, 90), 4),
                "lng": round(rng.uniform(-180, 180), 4),
                "duration_ms": rng.randint(1, 3500),
            },
        }
        out.append(json.dumps(ev))
    (workspace / "events.jsonl").write_text("\n".join(out) + "\n")


def _gen_q03_metrics(workspace: Path, rng: random.Random) -> None:
    """CSV of host metrics, ~15 MB / 425k rows."""
    metric_names = ["cpu_pct", "mem_pct", "disk_io", "net_in", "net_out", "load_1m"]
    hosts = [f"host-{i:03d}" for i in range(0, 100)]
    epoch = 1_710_000_000
    rows = ["timestamp,host,metric,value"]
    for i in range(425_000):
        ts = epoch + i
        h = rng.choice(hosts)
        m = rng.choice(metric_names)
        if m in ("cpu_pct", "mem_pct"):
            v = round(rng.uniform(0.5, 99.5), 2)
        elif m == "load_1m":
            v = round(rng.uniform(0.0, 16.0), 3)
        else:
            v = round(rng.uniform(0, 100_000), 2)
        rows.append(f"{ts},{h},{m},{v}")
    (workspace / "metrics.csv").write_text("\n".join(rows) + "\n")


def _gen_q04_transactions(workspace: Path, rng: random.Random) -> None:
    """Transaction log with deliberate duplicates; ~12 MB."""
    lines: list[str] = []
    epoch = 1_710_000_000
    # Generate 70k unique txn ids; then duplicate ~12 of them so the answer is non-empty.
    n_unique = 70_000
    txn_ids = [f"TXN-{rng.randint(10_000_000, 99_999_999)}" for _ in range(n_unique)]
    seen: set[str] = set()
    uniq_txns: list[str] = []
    for t in txn_ids:
        if t not in seen:
            seen.add(t)
            uniq_txns.append(t)
    txn_ids = uniq_txns
    duplicates = rng.sample(txn_ids, 12)

    sequence = list(txn_ids) + duplicates  # duplicates appear a second time
    rng.shuffle(sequence)

    for i, tid in enumerate(sequence):
        ts = epoch + i
        amount = round(rng.uniform(0.5, 9_999.99), 2)
        currency = rng.choice(["USD", "EUR", "GBP", "JPY"])
        status = rng.choice(["OK", "OK", "OK", "PENDING", "FAILED"])
        merchant = rng.choice(["acme", "globex", "initech", "stark", "umbrella", "wayne"])
        # Pad to bring file size up.
        notes = "".join(rng.choices(string.ascii_letters + " ", k=rng.randint(40, 110)))
        lines.append(
            f"ts={ts} txn_id={tid} amount={amount:.2f} ccy={currency} "
            f"status={status} merchant={merchant} note={notes!r}"
        )
    (workspace / "transactions.log").write_text("\n".join(lines) + "\n")
    (workspace / "duplicate_count.hint").write_text(
        "Hint for the grader, not the agent: 12 duplicate transaction ids exist.\n"
    )


def _gen_q05_audit(workspace: Path, rng: random.Random) -> None:
    """Audit log; one user is engineered to have the most distinct actions."""
    actions = [
        "create_user", "delete_user", "update_user", "view_user",
        "create_role", "delete_role", "update_role", "view_role",
        "grant_perm", "revoke_perm", "list_perm",
        "login", "logout", "rotate_secret", "view_audit", "export_data",
    ]
    resources = [f"resource-{i:04d}" for i in range(0, 200)]
    users = [f"user-{c}" for c in string.ascii_lowercase[:18]]
    epoch = 1_710_000_000
    lines = []
    # Bias action diversity heavily toward user-q (but mix in others to keep ambiguity).
    target_user = "user-q"
    for i in range(120_000):
        ts = epoch + i * 3
        if i % 11 == 0:
            user = target_user
            action = rng.choice(actions)  # all actions
        else:
            user = rng.choice([u for u in users if u != target_user])
            action = rng.choice(actions[:8])  # narrower set so target wins on distinct count
        resource = rng.choice(resources)
        result = rng.choice(["allowed", "allowed", "allowed", "denied"])
        lines.append(f"ts={ts} user={user} action={action} resource={resource} result={result}")
    (workspace / "audit.log").write_text("\n".join(lines) + "\n")


# ---- Category B: multi-file Python projects -----------------------------------


def _py_class(name: str, base: str, methods: list[str]) -> str:
    body = "\n".join(f"    def {m}(self): return {m!r}" for m in methods) or "    pass"
    return f"class {name}({base}):\n{body}\n"


def _gen_q06(workspace: Path) -> None:
    """12 files. 4 classes inherit BaseHandler; the rest do not."""
    base_file = workspace / "base.py"
    base_file.write_text("class BaseHandler:\n    def handle(self): pass\n")
    handler_specs = [
        ("LoginHandler", ["login", "logout", "validate"]),
        ("PaymentHandler", ["charge", "refund", "verify"]),
        ("CartHandler", ["add", "remove", "checkout"]),
        ("AdminHandler", ["promote", "demote"]),
    ]
    for cls, methods in handler_specs:
        f = workspace / f"{cls.lower()}.py"
        f.write_text(
            f"from base import BaseHandler\n\n"
            f"{_py_class(cls, 'BaseHandler', methods)}\n"
        )
    decoys = ["util_a", "util_b", "util_c", "logger", "config", "metrics", "errors"]
    for n in decoys:
        (workspace / f"{n}.py").write_text(
            f"# Decoy module {n}\n\nclass {n.title().replace('_','')}Helper:\n    def run(self): return {n!r}\n"
        )


def _gen_q07(workspace: Path) -> None:
    """10 files referencing legacy_utils to be renamed compat_utils."""
    legacy = workspace / "legacy_utils.py"
    legacy.write_text(
        "def add(a, b): return a + b\n"
        "def slugify(s): return s.lower().replace(' ', '-')\n"
        "VERSION = '1.0'\n"
    )
    callers = [
        ("api.py", "from legacy_utils import add, slugify\n\ndef route(s): return slugify(s)\n"),
        ("worker.py", "import legacy_utils\n\ndef job(): return legacy_utils.VERSION\n"),
        ("billing.py", "from legacy_utils import add\n\ndef total(a, b): return add(a, b)\n"),
        ("admin.py", "from legacy_utils import slugify\n\ndef name_to_slug(n): return slugify(n)\n"),
        ("reports.py", "from legacy_utils import VERSION\n\ndef report_v(): return VERSION\n"),
        ("scheduler.py", "import legacy_utils as lu\n\ndef ver(): return lu.VERSION\n"),
        ("cli.py", "from legacy_utils import add, slugify, VERSION\n\nprint(add(1,2), slugify('Hi'), VERSION)\n"),
        ("readme.txt", "Project uses legacy_utils for slugification and adders.\nSee legacy_utils.py.\n"),
        ("settings.py", "# Bookkeeping module — does not touch legacy_utils.\nDEBUG = True\n"),
    ]
    for name, body in callers:
        (workspace / name).write_text(body)


def _gen_q08(workspace: Path) -> None:
    """15 files; 5 functions accept request and don't validate, 6 do."""
    (workspace / "validators.py").write_text(
        "def validate_request(req):\n    if not req: raise ValueError('empty')\n    return True\n"
    )
    safe_specs = [
        ("auth.py", "login", True),
        ("orders.py", "place_order", True),
        ("admin_users.py", "delete_user", True),
        ("billing_charges.py", "charge", True),
        ("uploads.py", "upload", True),
        ("cart.py", "checkout", True),
    ]
    unsafe_specs = [
        ("debug_dump.py", "dump", False),
        ("metrics_emit.py", "emit", False),
        ("legacy_handler.py", "handle_legacy", False),
        ("ping.py", "ping", False),
        ("misc_route.py", "misc", False),
    ]
    for fname, fn, validates in safe_specs + unsafe_specs:
        if validates:
            body = (
                "from validators import validate_request\n\n"
                f"def {fn}(request):\n"
                f"    validate_request(request)\n"
                f"    return {fn!r}\n"
            )
        else:
            body = f"def {fn}(request):\n    return request and {fn!r}\n"
        (workspace / fname).write_text(body)
    decoys = ["docs.py", "constants.py", "schema.py"]
    for d in decoys:
        (workspace / d).write_text(f"# {d} — no request handlers\nVALUE = 42\n")


def _gen_q09(workspace: Path) -> None:
    """8 files; mod_a -> mod_b -> mod_c -> mod_a is the cycle. Plus normal deps."""
    files = {
        "mod_a.py": "import mod_b\n\ndef a(): return mod_b.b()\n",
        "mod_b.py": "import mod_c\n\ndef b(): return mod_c.c()\n",
        "mod_c.py": "import mod_a\n\ndef c(): return mod_a.a()\n",
        "mod_d.py": "import mod_e\n\ndef d(): return mod_e.e()\n",
        "mod_e.py": "def e(): return 'e'\n",
        "main.py": "import mod_a\nimport mod_d\n\nprint(mod_a.a(), mod_d.d())\n",
        "helpers.py": "def help_(): return 'help'\n",
        "sentinel.py": "X = 1\n",
    }
    for name, body in files.items():
        (workspace / name).write_text(body)


def _gen_q10(workspace: Path) -> None:
    """14 files with TODOs grouped by 4 different authors, plus 2 unattributed."""
    todo_items = [
        ("alice", "wire up retry logic"),
        ("alice", "stop swallowing errors here"),
        ("alice", "add metrics"),
        ("alice", "verify input length"),
        ("bob", "switch to async client"),
        ("bob", "drop the legacy fallback"),
        ("bob", "refactor this monstrosity"),
        ("carol", "remove once compat_utils lands"),
        ("carol", "split into two files"),
        ("dave", "add a docstring"),
    ]
    for i, (author, msg) in enumerate(todo_items):
        body = (
            f"# Source file {i:02d}\n"
            f"\n"
            f"def fn_{i:02d}():\n"
            f"    # TODO({author}): {msg}\n"
            f"    return {i}\n"
        )
        (workspace / f"src_{i:02d}.py").write_text(body)
    # 2 unattributed TODOs (loose match, not author-tagged).
    (workspace / "untagged_todo_a.py").write_text(
        "def fn_a():\n    # TODO: figure this out\n    return 1\n"
    )
    (workspace / "untagged_todo_b.py").write_text(
        "def fn_b():\n    # TODO: maybe later\n    return 2\n"
    )
    # 2 plain decoys.
    (workspace / "config.py").write_text("VALUE = 1\n")
    (workspace / "consts.py").write_text("PI = 3.14159\n")


# ---- Category C: bug diagnosis -----------------------------------------------


_RUNTESTS_TEMPLATE = """#!/usr/bin/env bash
# Custom test runner — not pytest. Output format the agent must parse:
#   TEST: <name> PASS|FAIL
#   TRACE: <file>:<line> <ExceptionType>: <message>
set -u
cd "$(dirname "$0")"

run_one() {{
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
}}

{calls}
"""


def _write_runtests(workspace: Path, calls: list[tuple[str, str]]) -> None:
    body = _RUNTESTS_TEMPLATE.format(
        calls="\n".join(f'run_one "{n}" \'{s}\'' for n, s in calls)
    )
    p = workspace / "runtests.sh"
    p.write_text(body)
    p.chmod(0o755)


def _gen_q11(workspace: Path) -> None:
    # Off-by-one bug in slicing a list.
    (workspace / "slicer.py").write_text(textwrap.dedent("""
        def first_n(seq, n):
            # BUG: should be seq[:n], not seq[:n-1]
            return seq[:n - 1]
    """).lstrip())
    calls = [
        ("test_first_n_three",
         "from slicer import first_n; assert first_n([1,2,3,4,5], 3) == [1,2,3], 'expected [1,2,3]'"),
        ("test_first_n_zero",
         "from slicer import first_n; assert first_n([1,2,3], 0) == []"),
    ]
    _write_runtests(workspace, calls)


def _gen_q12(workspace: Path) -> None:
    # Wrong key in dict access across 3 files.
    (workspace / "shapes.py").write_text(
        "SQUARE = {'sides': 4, 'area_fn': 'side*side'}\n"
        "TRIANGLE = {'sides': 3, 'area_fn': '0.5*base*height'}\n"
    )
    (workspace / "describe.py").write_text(textwrap.dedent("""
        from shapes import SQUARE, TRIANGLE

        def describe(shape):
            # BUG: should be 'sides', not 'side'.
            return f\"{shape['side']} sides\"
    """).lstrip())
    (workspace / "main.py").write_text(
        "from describe import describe\nfrom shapes import SQUARE\n\nprint(describe(SQUARE))\n"
    )
    calls = [
        ("test_square_describe",
         "from describe import describe; from shapes import SQUARE; "
         "assert describe(SQUARE) == '4 sides'"),
        ("test_triangle_describe",
         "from describe import describe; from shapes import TRIANGLE; "
         "assert describe(TRIANGLE) == '3 sides'"),
    ]
    _write_runtests(workspace, calls)


def _gen_q13(workspace: Path) -> None:
    # Type confusion: comparing int to str.
    (workspace / "counter.py").write_text(textwrap.dedent("""
        def is_positive(value):
            # BUG: comparing string '0' to int 0 raises TypeError on int input.
            return value > '0'
    """).lstrip())
    calls = [
        ("test_positive_int",
         "from counter import is_positive; assert is_positive(5)"),
        ("test_zero_int",
         "from counter import is_positive; assert not is_positive(0)"),
    ]
    _write_runtests(workspace, calls)


def _gen_q14(workspace: Path) -> None:
    # Missing edge case: empty input.
    (workspace / "stats.py").write_text(textwrap.dedent("""
        def mean(xs):
            # BUG: doesn't handle empty list (ZeroDivisionError).
            return sum(xs) / len(xs)
    """).lstrip())
    calls = [
        ("test_mean_three",
         "from stats import mean; assert mean([1,2,3]) == 2.0"),
        ("test_mean_empty",
         "from stats import mean; assert mean([]) == 0.0"),
    ]
    _write_runtests(workspace, calls)


def _gen_q15(workspace: Path) -> None:
    # Operator precedence bug.
    (workspace / "perm.py").write_text(textwrap.dedent("""
        def can_access(is_admin, is_owner, is_locked):
            # BUG: precedence groups (is_admin or is_owner) and not is_locked
            # the wrong way; what's intended is admin/owner AND not locked.
            return is_admin or is_owner and not is_locked
    """).lstrip())
    calls = [
        ("test_admin_locked_should_be_denied",
         "from perm import can_access; assert not can_access(True, False, True)"),
        ("test_owner_unlocked",
         "from perm import can_access; assert can_access(False, True, False)"),
    ]
    _write_runtests(workspace, calls)


# ---- Category D: aggregate analysis ------------------------------------------


def _gen_q16(workspace: Path) -> None:
    """15 config files in mixed formats with varied timeout fields."""
    configs = [
        ("svc_a.json", '{"timeout": 30, "timeout_unit": "seconds", "name": "svc_a"}\n'),
        ("svc_b.json", '{"name": "svc_b", "retry": 3}\n'),  # no timeout
        ("svc_c.json", '{"timeout": 1500, "timeout_unit": "ms", "name": "svc_c"}\n'),
        ("svc_d.yaml", "name: svc_d\ntimeout: 10\ntimeout_unit: seconds\n"),
        ("svc_e.yaml", "name: svc_e\nretries: 5\n"),  # no timeout
        ("svc_f.yaml", "name: svc_f\ntimeout: 750\ntimeout_unit: milliseconds\n"),
        ("svc_g.toml", '[svc]\nname = "svc_g"\ntimeout = 60\ntimeout_unit = "seconds"\n'),
        ("svc_h.toml", '[svc]\nname = "svc_h"\nbackend = "primary"\n'),  # no timeout
        ("svc_i.toml", '[svc]\nname = "svc_i"\ntimeout = 200\ntimeout_unit = "ms"\n'),
        ("svc_j.ini", "[svc]\nname = svc_j\ntimeout = 5\ntimeout_unit = seconds\n"),
        ("svc_k.ini", "[svc]\nname = svc_k\nverbose = true\n"),  # no timeout
        ("svc_l.ini", "[svc]\nname = svc_l\ntimeout = 12000\ntimeout_unit = ms\n"),
        ("svc_m.json", '{"timeout": 2, "timeout_unit": "minutes", "name": "svc_m"}\n'),
        ("svc_n.yaml", "name: svc_n\ntimeout: 90\ntimeout_unit: seconds\n"),
        ("svc_o.toml", '[svc]\nname = "svc_o"\ntimeout = 100\ntimeout_unit = "ms"\n'),
    ]
    for name, body in configs:
        (workspace / name).write_text(body)


def _gen_q17(workspace: Path) -> None:
    """20 small Python files with varied raise statements."""
    exc_types = [
        "ValueError", "TypeError", "KeyError", "IndexError",
        "RuntimeError", "NotImplementedError", "PermissionError",
    ]
    for i in range(20):
        # Each file raises 1-3 distinct exceptions, biased to overlap so the
        # answer is non-trivial.
        rng = random.Random(SEED_BASE + 1000 + i)
        n = rng.randint(1, 3)
        chosen = rng.sample(exc_types, n)
        body_lines = [f"# module_{i:02d}.py"]
        for j, exc in enumerate(chosen):
            body_lines.append(f"def fn_{i:02d}_{j}(x):")
            body_lines.append(f"    if x is None:")
            body_lines.append(f"        raise {exc}('bad x in fn_{i:02d}_{j}')")
            body_lines.append(f"    return x")
        (workspace / f"module_{i:02d}.py").write_text("\n".join(body_lines) + "\n")


def _gen_q18(workspace: Path) -> None:
    """12 markdown files, varied heading hierarchies."""
    docs = [
        ("intro.md", "# Intro\n\n## Goals\n\n## Non-Goals\n\n# Setup\n\n## Local\n\n## CI\n"),
        ("api.md", "# API\n\n## Endpoints\n\n### GET /users\n\n### POST /users\n\n## Errors\n"),
        ("design.md", "# Design\n\n## Storage\n\n## Compute\n\n### Workers\n\n### Queue\n"),
        ("ops.md", "# Operations\n\n## Deploy\n\n## Rollback\n\n## Oncall\n"),
        ("changelog.md", "# Changelog\n\n## v1.2\n\n## v1.1\n\n## v1.0\n"),
        ("faq.md", "# FAQ\n\n## Why X?\n\n## How to Y?\n\n## When does Z?\n"),
        ("guide.md", "# Guide\n\n## Quickstart\n\n### Install\n\n### Hello\n\n## Advanced\n"),
        ("metrics.md", "# Metrics\n\n## Latency\n\n## Throughput\n\n## Errors\n"),
        ("security.md", "# Security\n\n## Threat Model\n\n## Mitigations\n"),
        ("arch.md", "# Architecture\n\n## Layers\n\n### Edge\n\n### Core\n\n### Data\n"),
        ("infra.md", "# Infra\n\n## Networking\n\n## Compute\n"),
        ("notes.md", "# Notes\n\n## TODOs\n\n## Ideas\n"),
    ]
    for name, body in docs:
        (workspace / name).write_text(body)


def _gen_q19(workspace: Path) -> None:
    """18 source files; deterministic import graph the agent must discover."""
    # Deterministic edges keyed by index.
    n = 18
    files = []
    for i in range(n):
        # Each file imports from i-1 and i-3 if those exist (plus a couple cross-cuts).
        deps = []
        if i - 1 >= 0:
            deps.append(i - 1)
        if i - 3 >= 0:
            deps.append(i - 3)
        if i % 5 == 0 and i + 7 < n:
            deps.append(i + 7)
        body = "\n".join(f"from src_{d:02d} import value as v_{d:02d}" for d in deps)
        body += f"\n\nvalue = {i}\n"
        if deps:
            body += f"def total(): return {' + '.join(f'v_{d:02d}' for d in deps)}\n"
        else:
            body += "def total(): return 0\n"
        files.append((f"src_{i:02d}.py", body))
    for name, body in files:
        (workspace / name).write_text(body)


def _gen_q20(workspace: Path) -> None:
    """10 small log fragments. Stitched by timestamp the first/last events are deterministic."""
    rng = random.Random(SEED_BASE + 2000)
    epoch = 1_710_000_000
    chunks: list[list[tuple[int, str]]] = [[] for _ in range(10)]
    msg_pool = [
        "service started", "warming caches", "leader elected",
        "client connected", "client disconnected", "task scheduled",
        "task completed", "snapshot saved", "checkpoint flushed",
        "config reloaded", "shutdown requested",
    ]
    n_total = 1000
    for i in range(n_total):
        ts = epoch + i * 7 + rng.randint(0, 5)
        msg = rng.choice(msg_pool)
        bucket = rng.randint(0, 9)
        chunks[bucket].append((ts, f"{ts} {msg}"))
    # Each fragment file is sorted internally.
    for idx, chunk in enumerate(chunks):
        chunk.sort()
        body = "\n".join(line for _, line in chunk) + "\n"
        (workspace / f"frag_{idx:02d}.log").write_text(body)


# ---- top-level dispatch -------------------------------------------------------


GENERATORS = {
    "q01": _gen_q01_access_log,
    "q02": _gen_q02_events,
    "q03": _gen_q03_metrics,
    "q04": _gen_q04_transactions,
    "q05": _gen_q05_audit,
    "q06": _gen_q06,
    "q07": _gen_q07,
    "q08": _gen_q08,
    "q09": _gen_q09,
    "q10": _gen_q10,
    "q11": _gen_q11,
    "q12": _gen_q12,
    "q13": _gen_q13,
    "q14": _gen_q14,
    "q15": _gen_q15,
    "q16": _gen_q16,
    "q17": _gen_q17,
    "q18": _gen_q18,
    "q19": _gen_q19,
    "q20": _gen_q20,
}

# Only Category A generators take an rng; everything else is deterministic.
TAKES_RNG = {"q01", "q02", "q03", "q04", "q05"}


def main() -> int:
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for i, (qid, gen) in enumerate(GENERATORS.items()):
        ws = WORKSPACES / qid
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True)
        if qid in TAKES_RNG:
            rng = random.Random(SEED_BASE + i)
            gen(ws, rng)  # type: ignore[arg-type]
        else:
            gen(ws)  # type: ignore[arg-type]
        # Tally bytes for sanity check.
        ws_bytes = sum(p.stat().st_size for p in ws.rglob("*") if p.is_file())
        total_bytes += ws_bytes
        n_files = sum(1 for _ in ws.iterdir())
        print(f"  {qid}: {ws_bytes / 1e6:6.2f} MB ({n_files} files)")

    print(f"\nTotal: {total_bytes / 1e6:.2f} MB across {len(GENERATORS)} workspaces")
    if total_bytes > 100 * 1024 * 1024:
        print(
            f"WARN: total > 100 MB ({total_bytes/1e6:.1f} MB). Consider trimming Category A.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
