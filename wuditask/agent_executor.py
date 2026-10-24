"""Durable subprocess attempts; executor success is only a candidate for review."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from .errors import WudiTaskError
from .util import atomic_write_json, read_json, utc_now

IDS = ("plan_id", "run_id", "item_id", "attempt_id")
WAKE_TIMEOUT = 30


@contextmanager
def _lock(directory: Path, name: str = ".executor.lock", blocking: bool = True):
    with (directory / name).open("a") as handle:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle, flags)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(path: Path, value: dict) -> None:
    atomic_write_json(path, value)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _optional(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise WudiTaskError("invalid_executor_record", f"Expected object: {path}")
    return value


def _ids(assignment: dict) -> dict:
    if any(not isinstance(assignment.get(k), str) or not assignment[k] for k in IDS):
        raise WudiTaskError(
            "invalid_assignment", "Assignment requires plan/run/item/attempt IDs"
        )
    return {key: assignment[key] for key in IDS}


def _argv(command: list[str] | None) -> list[str] | None:
    if command is not None and (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
    ):
        raise WudiTaskError("invalid_command", "Command must be a nonempty argv list")
    return command


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(pid: int) -> dict:
    """Linux boot ID and start ticks distinguish a PID from a reused PID."""
    identity = {"pid": pid}
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2 :].split()
        identity.update(
            start_ticks=fields[19],
            state=fields[0],
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        )
    except (OSError, IndexError):
        pass
    return identity


def _process_state(identity: dict | None) -> str:
    if not identity or not isinstance(identity.get("pid"), int):
        return "unknown"
    if not identity.get("start_ticks") or not identity.get("boot_id"):
        return "unknown"
    observed = _identity(identity["pid"])
    if not observed.get("start_ticks"):
        return (
            "exited"
            if Path("/proc").is_dir() and not Path(f"/proc/{identity['pid']}").exists()
            else "unknown"
        )
    if any(observed.get(key) != identity[key] for key in ("boot_id", "start_ticks")):
        return "exited"
    return "exited" if observed.get("state") in ("Z", "X") else "running"


def _backend_argv(
    backend: str, session_id: str, resume: bool = False, launcher: str | None = None
) -> list[str]:
    executable = backend
    if launcher is not None:
        if not isinstance(launcher, str) or not launcher or "\0" in launcher:
            raise WudiTaskError(
                "invalid_launcher", "Launcher must be one executable file path"
            )
        try:
            path = Path(launcher).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WudiTaskError(
                "invalid_launcher", "Launcher file does not exist or cannot be resolved"
            ) from exc
        if not path.is_file() or not os.access(path, os.X_OK):
            raise WudiTaskError(
                "invalid_launcher", "Launcher must be an executable regular file"
            )
        executable = str(path)
    if backend == "claude":
        try:
            uuid.UUID(session_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise WudiTaskError(
                "invalid_session", "Claude session ID must be a UUID"
            ) from exc
        return [
            executable,
            "-p",
            "--resume" if resume else "--session-id",
            session_id,
            "--output-format",
            "json",
        ]
    if backend == "codex":
        if resume:
            raise WudiTaskError(
                "unsupported_resume", "Codex subprocess continuation is not supported"
            )
        return [executable, "exec", "--json", "-"]
    raise WudiTaskError("invalid_executor", "Process backend must be claude or codex")


def launch(
    attempt_dir: Path,
    backend: str,
    cwd: Path,
    prompt_path: Path,
    session_id: str,
    check_command: list[str] | None = None,
    wake_command: list[str] | None = None,
    resume: bool = False,
    launcher: str | None = None,
) -> dict:
    directory = Path(attempt_dir).resolve()
    with _lock(directory):
        existing = _optional(directory / "launch.json")
        if existing is not None:
            return existing
        argv = _backend_argv(backend, session_id, resume, launcher)
        _argv(check_command)
        _argv(wake_command)
        cwd, prompt_path = Path(cwd).resolve(), Path(prompt_path).resolve()
        if not cwd.is_dir() or not prompt_path.is_file():
            raise WudiTaskError(
                "invalid_executor_input", "Workspace and prompt must exist"
            )
        assignment = read_json(directory / "assignment.json")
        record = {
            **_ids(assignment),
            "backend": backend,
            "session_id": session_id,
            "resume": resume,
            "launcher": argv[0],
            "cwd": str(cwd),
            "prompt_path": str(prompt_path),
            "prompt_digest": _digest(prompt_path),
            "argv": argv,
            "argv_digest": hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
            "check_command": check_command,
            "wake_command": wake_command,
            "status": "launch_unknown",
            "phase": "intent",
            "created_at": utc_now(),
        }
        # The lock excludes competing starts; replacement is forbidden after any intent.
        _write(directory / "launch.json", record)
        environment = os.environ.copy()
        root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = (
            root + os.pathsep + environment.get("PYTHONPATH", "")
        )
        child = None
        try:
            with (directory / "supervisor.log").open("ab") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "wuditask.agent_executor",
                        "supervise",
                        str(directory),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    close_fds=True,
                    env=environment,
                )
            threading.Thread(target=child.wait, daemon=True).start()
            record.update(supervisor=_identity(child.pid), status="starting")
            _write(directory / "launch.json", record)
        except OSError as exc:
            record.update(
                status="rejected" if child is None else "launch_unknown",
                error=type(exc).__name__,
            )
            _write(directory / "launch.json", record)
            if child is None:
                _finish(
                    directory,
                    record,
                    "executor_failed",
                    None,
                    "supervisor_spawn_failed",
                )
        except Exception as exc:
            # A transport/parent failure can happen after the OS accepted the spawn.
            record.update(status="launch_unknown", error=type(exc).__name__)
            _write(directory / "launch.json", record)
        return record


def _parse_output(backend: str, output: str, session_id: str) -> dict:
    try:
        if backend == "claude":
            payload = json.loads(output)
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            if payload.get("session_id") != session_id:
                return {"outcome": "executor_failed", "reason": "session_mismatch"}
            if payload.get("is_error") is not False:
                return {
                    "outcome": "executor_failed",
                    "reason": "claude_error_or_missing_status",
                }
            return {
                "outcome": "candidate",
                "session_id": session_id,
                "response": payload.get("result"),
            }
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("Expected JSONL objects")
        if any(event.get("type") in ("error", "turn.failed") for event in events):
            return {"outcome": "executor_failed", "reason": "codex_error"}
        threads = [
            event.get("thread_id")
            for event in events
            if event.get("type") == "thread.started"
        ]
        if (
            not threads
            or not isinstance(threads[0], str)
            or not threads[0]
            or not any(event.get("type") == "turn.completed" for event in events)
        ):
            return {"outcome": "result_missing", "reason": "codex_incomplete_output"}
        return {"outcome": "candidate", "thread_id": threads[0]}
    except (json.JSONDecodeError, ValueError):
        return {"outcome": "result_missing", "reason": "invalid_or_missing_output"}


def _publish_completion(directory: Path, record: dict, result: dict) -> dict:
    if (
        _ids(result) != _ids(record)
        or result.get("accepted") is not False
        or result.get("outcome")
        not in ("candidate", "executor_failed", "result_missing")
    ):
        raise WudiTaskError(
            "invalid_executor_result", "Executor result identity or outcome is invalid"
        )
    event = {
        **_ids(record),
        "event_seq": 1,
        "kind": result["outcome"],
        "outcome": result["outcome"],
        "result_path": "executor-result.json",
        "result_digest": _digest(directory / "executor-result.json"),
        "created_at": utc_now(),
    }
    if not (directory / "exit.json").exists():
        _write(
            directory / "exit.json", {**event, "returncode": result.get("returncode")}
        )
    if not (directory / "completion.json").exists():
        _write(directory / "completion.json", event)
    return read_json(directory / "completion.json")


def _finish(
    directory: Path,
    record: dict,
    outcome: str,
    returncode: int | None,
    reason: str | None = None,
    parsed: dict | None = None,
) -> dict:
    result = _optional(directory / "executor-result.json")
    if result is None:
        result = {
            **_ids(record),
            **(parsed or {}),
            "outcome": outcome,
            "returncode": returncode,
            "reason": reason or (parsed or {}).get("reason"),
            "stdout_path": "stdout.log",
            "stderr_path": "stderr.log",
            "accepted": False,
            "created_at": utc_now(),
        }
        _write(directory / "executor-result.json", result)
    return _publish_completion(directory, record, result)


def _supervise(directory: Path) -> None:
    # A second supervisor invocation must never become a second writer.
    try:
        with _lock(directory, ".supervisor.lock", blocking=False):
            _run_supervisor(directory)
    except BlockingIOError:
        return


def _run_supervisor(directory: Path) -> None:
    with _lock(directory):
        record = read_json(directory / "launch.json")
        if (
            record.get("supervisor_started_at")
            or (directory / "completion.json").exists()
        ):
            return
        record.update(
            supervisor=_identity(os.getpid()),
            supervisor_started_at=utc_now(),
            phase="checking",
        )
        _write(directory / "launch.json", record)
    outcome, returncode, reason, parsed = "executor_failed", None, None, None
    child = None
    try:
        if _digest(Path(record["prompt_path"])) != record["prompt_digest"]:
            raise ValueError("prompt_changed")
        check = record.get("check_command")
        if check:
            with (
                (directory / "check.stdout.log").open("wb") as stdout,
                (directory / "check.stderr.log").open("wb") as stderr,
            ):
                checked = subprocess.run(
                    check,
                    cwd=record["cwd"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=60,
                    check=False,
                )
            if checked.returncode:
                reason = "execution_check_failed"
                returncode = checked.returncode
                return
        with _lock(directory):
            record.update(phase="spawning")
            _write(directory / "launch.json", record)
        with (
            Path(record["prompt_path"]).open("rb") as prompt,
            (directory / "stdout.log").open("wb") as stdout,
            (directory / "stderr.log").open("wb") as stderr,
        ):
            child = subprocess.Popen(
                record["argv"],
                cwd=record["cwd"],
                stdin=prompt,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
            )
            with _lock(directory):
                record.update(
                    child=_identity(child.pid), phase="running", status="running"
                )
                _write(directory / "launch.json", record)
            returncode = child.wait()
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
        parsed = _parse_output(
            record["backend"],
            (directory / "stdout.log").read_text(errors="replace"),
            record["session_id"],
        )
        outcome = parsed["outcome"] if returncode == 0 else "executor_failed"
        reason = "nonzero_exit" if returncode else parsed.get("reason")
    except Exception as exc:
        # Once spawned, no terminal event may free the writer until it exits.
        if child is not None:
            returncode = child.wait()
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    finally:
        with _lock(directory):
            _finish(directory, record, outcome, returncode, reason, parsed)
            record.update(status="exited", phase="finished", finished_at=utc_now())
            _write(directory / "launch.json", record)
        wake(directory, record.get("wake_command"))


def inspect(attempt_dir: Path) -> dict:
    directory = Path(attempt_dir)
    with _lock(directory):
        return _inspect(directory)


def _inspect(directory: Path) -> dict:
    record = _optional(directory / "launch.json")
    completion = _optional(directory / "completion.json")
    if record is None:
        return {"status": "not_started"}
    supervisor = _process_state(record.get("supervisor"))
    child = _process_state(record.get("child"))
    status = (
        "exited"
        if completion
        else "running"
        if "running" in (supervisor, child)
        else "unknown"
    )
    return {
        "status": status,
        "launch": record,
        "supervisor_state": supervisor,
        "child_state": child,
        "completion": completion,
        "result": _optional(directory / "executor-result.json"),
        "wake": _optional(directory / "wake.json"),
    }


def reconcile(attempt_dir: Path) -> dict:
    directory = Path(attempt_dir)
    with _lock(directory):
        state = _inspect(directory)
        record = state.get("launch")
        if record and not state["completion"]:
            result = state.get("result")
            if result:
                _publish_completion(directory, record, result)
            elif (
                state["supervisor_state"] == "exited"
                and state["child_state"] != "running"
            ):
                # A crash inside spawn may leave an unrecorded live child.
                safe = (
                    state["child_state"] == "exited"
                    or record.get("phase") == "checking"
                )
                if safe:
                    _finish(
                        directory,
                        record,
                        "result_missing",
                        None,
                        "supervisor_exited_without_result",
                    )
            state = _inspect(directory)
        return state


def wake(attempt_dir: Path, command: list[str] | None) -> dict:
    directory = Path(attempt_dir)
    _argv(command)
    with _lock(directory):
        previous = _optional(directory / "wake.json")
        if previous is not None and not (
            previous.get("status") == "manual" and command
        ):
            return previous
        record = read_json(directory / "launch.json")
        receipt = {
            **_ids(record),
            "status": "unknown" if command else "manual",
            "manual_resume_required": True,
            "created_at": utc_now(),
            "command": command,
        }
        _write(directory / "wake.json", receipt)
    if command is None:
        return receipt
    try:
        with (
            (directory / "wake.stdout.log").open("wb") as stdout,
            (directory / "wake.stderr.log").open("wb") as stderr,
        ):
            result = subprocess.run(
                command,
                cwd=record["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=WAKE_TIMEOUT,
                check=False,
            )
        receipt.update(
            status="accepted" if result.returncode == 0 else "rejected",
            returncode=result.returncode,
            manual_resume_required=result.returncode != 0,
        )
    except OSError as exc:
        receipt.update(status="rejected", error=type(exc).__name__)
    except Exception as exc:
        receipt.update(status="unknown", error=type(exc).__name__)
    with _lock(directory):
        _write(directory / "wake.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Internal durable process supervisor")
    parser.add_argument("action", choices=["supervise"])
    parser.add_argument("attempt_dir", type=Path)
    args = parser.parse_args()
    _supervise(args.attempt_dir.resolve())


if __name__ == "__main__":
    main()
