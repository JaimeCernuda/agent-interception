"""SWE-Agent tools (Python side).

Three instrumented tools matching the spec in EVAL_PLAN / Phase-1 design:

  - bash_run:   bash subprocess execution. The wrapper span tool.bash_run
                contains two children:
                  * tool.bash_spawn  — fork+exec window
                  * tool.bash_work   — subprocess running until exit + pipes drained
                This decomposition is the headline measurement: cross-language
                spawn cost is the most likely place a Python agent will look
                different from a Go agent under concurrency.
  - read_file:  read up to 50 KB from a file. One span, no decomposition.
  - write_file: write content to a file (creating parent dirs). One span.

All three respect a per-query workspace_dir: every path argument is resolved
relative to it AND constrained to it (no escaping via "..").

Stdout/stderr are capped at 10 KB each in the JSON returned to the agent. The
full byte counts are recorded on the bash_work span as
`bash.stdout_bytes` / `bash.stderr_bytes`.

Shell wrapping: by default we tokenize with shlex and exec the program
directly (no shell). If the command contains shell metacharacters
(``|``, ``>``, ``<``, ``&&``, ``||``, ``;``), we wrap with `bash -c <cmd>`
and tag the span `bash.shell_wrapped: True`. This matches realistic
SWE-Agent behavior.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from benchmark.obs import Observer, input_hash

_STDIO_CAP_BYTES = 10 * 1024
_DEFAULT_TIMEOUT_S = 30
_MAX_TIMEOUT_S = 30
_READ_FILE_CAP_BYTES = 50 * 1024
# Substrings that force a `bash -c` wrap. The order matters for the prefix
# scan: longest first. We do a substring check here, not a tokenized parse,
# because shell-redirection metachars are NOT preserved through shlex.split.
_SHELL_METACHARS = ("&&", "||", ";", "|", ">", "<")


def _shell_wrap(command: str) -> bool:
    return any(m in command for m in _SHELL_METACHARS)


def _safe_workspace_path(workspace_dir: Path, raw_path: str) -> Path:
    """Resolve `raw_path` against `workspace_dir` and refuse to escape it."""
    workspace_dir = workspace_dir.resolve()
    candidate = (workspace_dir / raw_path).resolve()
    try:
        candidate.relative_to(workspace_dir)
    except ValueError as e:
        raise ValueError(
            f"path {raw_path!r} escapes workspace {workspace_dir}"
        ) from e
    return candidate


def _truncate_bytes(data: bytes, cap: int) -> tuple[str, bool]:
    """Decode `data` (utf-8, replace) capped at `cap` bytes; return (text, truncated)."""
    if len(data) <= cap:
        return data.decode("utf-8", errors="replace"), False
    head = data[:cap]
    return head.decode("utf-8", errors="replace") + "\n... [truncated]", True


def bash_run(
    command: str,
    workspace_dir: Path,
    obs: Observer,
    timeout_seconds: int | None = None,
) -> dict:
    """Run a bash command inside `workspace_dir`. Emits the bash_run/spawn/work span tree.

    Returns: {stdout, stderr, exit_code, timed_out, stdout_truncated, stderr_truncated}.
    """
    if timeout_seconds is None:
        timeout_seconds = _DEFAULT_TIMEOUT_S
    timeout_seconds = max(1, min(int(timeout_seconds), _MAX_TIMEOUT_S))

    shell_wrap = _shell_wrap(command)
    with obs.span(
        "tool.bash_run",
        **{
            "tool.name": "bash_run",
            "tool.input_hash": input_hash(command),
            "bash.command_preview": command[:200],
            "bash.shell_wrapped": shell_wrap,
            "bash.timeout_seconds": timeout_seconds,
        },
    ) as outer:
        try:
            if shell_wrap:
                args = ["/bin/bash", "-c", command]
            else:
                args = shlex.split(command)
                if not args:
                    raise ValueError("empty command")
        except ValueError as e:
            outer.set("tool.error", f"argv_parse: {e}")
            return {
                "stdout": "",
                "stderr": f"argv parse error: {e}",
                "exit_code": -1,
                "timed_out": False,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }

        # ---- bash_spawn child span ----
        spawn_start = time.time_ns()
        cpu_spawn_start = time.process_time_ns()
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
        except (OSError, ValueError) as e:
            spawn_end = time.time_ns()
            cpu_spawn_end = time.process_time_ns()
            obs.emit_synthetic_span(
                "tool.bash_spawn",
                start_ns=spawn_start,
                end_ns=spawn_end,
                cpu_start_ns=cpu_spawn_start,
                cpu_end_ns=cpu_spawn_end,
                **{
                    "tool.name": "bash_spawn",
                    "bash.spawn_error": str(e),
                },
            )
            outer.set("tool.error", f"spawn: {e}")
            return {
                "stdout": "",
                "stderr": f"spawn error: {e}",
                "exit_code": -1,
                "timed_out": False,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        spawn_end = time.time_ns()
        cpu_spawn_end = time.process_time_ns()
        obs.emit_synthetic_span(
            "tool.bash_spawn",
            start_ns=spawn_start,
            end_ns=spawn_end,
            cpu_start_ns=cpu_spawn_start,
            cpu_end_ns=cpu_spawn_end,
            **{
                "tool.name": "bash_spawn",
                "bash.shell_wrapped": shell_wrap,
                "bash.pid": int(proc.pid),
            },
        )

        # ---- bash_work child span ----
        work_start = spawn_end
        cpu_work_start = cpu_spawn_end
        timed_out = False
        stdout_b = b""
        stderr_b = b""
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout_seconds)
            exit_code = int(proc.returncode)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                stdout_b, stderr_b = proc.communicate(timeout=2)
            except Exception:
                stdout_b, stderr_b = b"", b""
            exit_code = -9
        work_end = time.time_ns()
        cpu_work_end = time.process_time_ns()

        obs.emit_synthetic_span(
            "tool.bash_work",
            start_ns=work_start,
            end_ns=work_end,
            cpu_start_ns=cpu_work_start,
            cpu_end_ns=cpu_work_end,
            **{
                "tool.name": "bash_work",
                "bash.exit_code": int(exit_code),
                "bash.timed_out": bool(timed_out),
                "bash.stdout_bytes": len(stdout_b),
                "bash.stderr_bytes": len(stderr_b),
            },
        )

        stdout_text, stdout_trunc = _truncate_bytes(stdout_b, _STDIO_CAP_BYTES)
        stderr_text, stderr_trunc = _truncate_bytes(stderr_b, _STDIO_CAP_BYTES)
        outer.set("bash.exit_code", int(exit_code))
        outer.set("bash.timed_out", bool(timed_out))
        outer.set("bash.stdout_bytes", len(stdout_b))
        outer.set("bash.stderr_bytes", len(stderr_b))
        outer.set("bash.stdout_truncated", stdout_trunc)
        outer.set("bash.stderr_truncated", stderr_trunc)
        return {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": int(exit_code),
            "timed_out": bool(timed_out),
            "stdout_truncated": stdout_trunc,
            "stderr_truncated": stderr_trunc,
        }


def read_file(
    path: str,
    workspace_dir: Path,
    obs: Observer,
) -> dict:
    """Read a file inside the workspace. Returns {content, truncated, size_bytes}."""
    with obs.span(
        "tool.read_file",
        **{
            "tool.name": "read_file",
            "tool.input_hash": input_hash(path),
            "tool.path": path,
        },
    ) as span:
        try:
            target = _safe_workspace_path(workspace_dir, path)
        except ValueError as e:
            span.set("tool.error", f"path: {e}")
            return {"content": "", "truncated": False, "size_bytes": 0, "error": str(e)}
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            span.set("tool.error", "not_found")
            return {"content": "", "truncated": False, "size_bytes": 0, "error": "not_found"}
        except OSError as e:
            span.set("tool.error", f"io: {e}")
            return {"content": "", "truncated": False, "size_bytes": 0, "error": f"io: {e}"}

        size = len(data)
        text, truncated = _truncate_bytes(data, _READ_FILE_CAP_BYTES)
        span.set("tool.size_bytes", size)
        span.set("tool.truncated", truncated)
        return {"content": text, "truncated": truncated, "size_bytes": size}


def write_file(
    path: str,
    content: str,
    workspace_dir: Path,
    obs: Observer,
) -> dict:
    """Write content to a file inside the workspace. Creates parent dirs."""
    with obs.span(
        "tool.write_file",
        **{
            "tool.name": "write_file",
            "tool.input_hash": input_hash(path),
            "tool.path": path,
        },
    ) as span:
        try:
            target = _safe_workspace_path(workspace_dir, path)
        except ValueError as e:
            span.set("tool.error", f"path: {e}")
            return {"ok": False, "size_bytes": 0, "error": str(e)}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            target.write_bytes(data)
        except OSError as e:
            span.set("tool.error", f"io: {e}")
            return {"ok": False, "size_bytes": 0, "error": f"io: {e}"}

        span.set("tool.size_bytes", len(data))
        return {"ok": True, "size_bytes": len(data)}


__all__ = ["bash_run", "read_file", "write_file"]


def _self_test() -> int:
    """Smoke test: bash_run, read_file, write_file all emit the expected spans."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        ws.mkdir()
        out = Path(td) / "out"
        out.mkdir()
        obs = Observer(config="sweagent_selftest", query_id="selftest", out_dir=out)
        with obs.root(query_text="self_test"):
            bash_run("echo hello world", ws, obs)
            bash_run("ls -la | head -5", ws, obs)  # shell wrap
            write_file("note.txt", "hi\n", ws, obs)
            read_file("note.txt", ws, obs)
        # Verify spans.
        trace_path = out / "selftest.json"
        assert trace_path.exists(), trace_path
        spans = json.loads(trace_path.read_text())["spans"]
        names = {s["name"] for s in spans}
        required = {
            "agent.query",
            "tool.bash_run",
            "tool.bash_spawn",
            "tool.bash_work",
            "tool.read_file",
            "tool.write_file",
        }
        missing = required - names
        if missing:
            print(f"FAIL self-test: missing spans {missing}")
            return 2
        # Two bash_run calls -> two spawn/work pairs.
        spawn_count = sum(1 for s in spans if s["name"] == "tool.bash_spawn")
        work_count = sum(1 for s in spans if s["name"] == "tool.bash_work")
        if spawn_count != 2 or work_count != 2:
            print(f"FAIL self-test: expected 2 spawn/2 work, got {spawn_count}/{work_count}")
            return 3
        print(f"OK self-test: {len(spans)} spans, names={sorted(names)}")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_test())
