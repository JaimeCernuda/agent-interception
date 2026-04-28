"""One-shot retroactive backfill: add agent.terminated_reason / agent.truncated
to root span attrs of existing trace JSONs that were written before those
attributes were emitted.

Heuristic: the last llm.generate span (by start_ns) in a trace with
stop_reason == 'tool_use' indicates the model wanted another turn but the
loop ended — i.e. _MAX_TURNS was reached. Any other stop_reason
('end_turn', 'stop_sequence', 'max_tokens') indicates a natural termination.

Idempotent: re-running on a trace that already has the attributes is a no-op.
Writes traces back in place with json.dump(indent=2) to preserve the original
file shape.

Usage:
    python -m benchmark.backfill_termination benchmark/results/cell_haiku_custom
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def backfill_one(path: Path) -> tuple[bool, str]:
    """Returns (changed, terminated_reason). changed=False if already had attrs."""
    blob = json.loads(path.read_text())
    spans = blob.get("spans", [])
    if not spans:
        return False, "empty"

    root = next((s for s in spans if s.get("parent_id") is None), None)
    if root is None:
        return False, "no_root"

    attrs = root.setdefault("attrs", {})
    if "agent.terminated_reason" in attrs:
        return False, str(attrs["agent.terminated_reason"])

    llm_spans = [
        s
        for s in spans
        if s.get("name") == "llm.generate"
        and not bool(s.get("attrs", {}).get("llm.rate_limited", False))
    ]
    if not llm_spans:
        # No LLM call at all — nothing to derive. Mark as unknown.
        attrs["agent.terminated_reason"] = "unknown"
        attrs["agent.truncated"] = False
        attrs["agent.last_stop_reason"] = ""
        path.write_text(json.dumps(blob, indent=2, default=str))
        return True, "unknown"

    llm_spans.sort(key=lambda s: int(s.get("start_ns", 0)))
    last = llm_spans[-1]
    last_stop = str(last.get("attrs", {}).get("llm.stop_reason", "") or "")
    terminated_reason = "max_turns" if last_stop == "tool_use" else "natural"

    attrs["agent.terminated_reason"] = terminated_reason
    attrs["agent.truncated"] = terminated_reason == "max_turns"
    attrs["agent.last_stop_reason"] = last_stop
    # max_turns value is not recoverable from the trace; record as derived-N/A
    attrs["agent.max_turns"] = -1  # sentinel: backfilled, original value unknown

    path.write_text(json.dumps(blob, indent=2, default=str))
    return True, terminated_reason


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    target = Path(argv[0])
    if not target.exists():
        print(f"ERROR: {target} not found", file=sys.stderr)
        return 2

    paths = sorted(target.glob("*.json"))
    if not paths:
        print(f"ERROR: no JSON traces in {target}", file=sys.stderr)
        return 2

    print(f"[backfill] scanning {len(paths)} traces in {target}")
    counts: dict[str, int] = {"natural": 0, "max_turns": 0, "unknown": 0}
    skipped = 0
    for p in paths:
        changed, reason = backfill_one(p)
        if not changed:
            skipped += 1
            print(f"  {p.name:>12s}  skip (already had attrs: {reason})")
        else:
            counts[reason] = counts.get(reason, 0) + 1
            tag = "TRUNCATED" if reason == "max_turns" else reason
            print(f"  {p.name:>12s}  {tag}")

    print()
    print(f"[backfill] done. updated {len(paths) - skipped}, skipped {skipped}.")
    print(f"[backfill] termination counts: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
