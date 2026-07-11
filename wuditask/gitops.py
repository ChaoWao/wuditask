from __future__ import annotations

import contextlib
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import WudiTaskError
from .model import Identity
from .repository import TaskRepository

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None

Operation = Callable[[TaskRepository], dict[str, Any]]
BeforePush = Callable[[int, Path], None]


class GitCoordinator:
    """Run optimistic Git transactions without force-pushing."""

    def __init__(
        self,
        *,
        local_root: Path | None = None,
        remote: str | None = None,
        branch: str | None = None,
        max_attempts: int = 5,
        before_push: BeforePush | None = None,
    ) -> None:
        if (local_root is None) == (remote is None):
            raise WudiTaskError(
                "invalid_git_coordinator",
                "Select exactly one task Hub source: local_root or remote.",
            )
        if remote is not None and (not branch or not branch.strip()):
            raise WudiTaskError(
                "invalid_git_coordinator",
                "A remote task Hub requires an explicit branch.",
            )
        self.root = local_root.resolve() if local_root is not None else None
        self.remote = remote.strip() if remote is not None else None
        self.branch = branch.strip() if branch is not None else None
        self.max_attempts = max_attempts
        self.before_push = before_push

    @property
    def distributed(self) -> bool:
        return self.remote is not None

    @contextlib.contextmanager
    def snapshot(self) -> Iterator[TaskRepository]:
        if not self.distributed:
            assert self.root is not None
            repository = TaskRepository(self.root)
            yield repository
            return
        with tempfile.TemporaryDirectory(prefix="wuditask-read-") as temporary:
            checkout = Path(temporary) / "hub"
            self._clone(checkout)
            yield TaskRepository(checkout)

    def write(
        self,
        operation: Operation,
        actor: Identity,
        commit_message: Callable[[dict[str, Any]], str],
    ) -> dict[str, Any]:
        if not self.distributed:
            assert self.root is not None
            with self._local_lock():
                repository = TaskRepository(self.root)
                result = operation(repository)
            result["sync"] = {
                "mode": "local",
                "confirmed": True,
                "attempts": 1,
            }
            return result

        last_rejection = ""
        for attempt in range(1, self.max_attempts + 1):
            with tempfile.TemporaryDirectory(prefix="wuditask-write-") as temporary:
                checkout = Path(temporary) / "hub"
                self._clone(checkout)
                repository = TaskRepository(checkout)
                result = operation(repository)
                if not result.get("changed", True):
                    result["sync"] = {
                        "mode": "remote",
                        "confirmed": True,
                        "attempts": attempt,
                        "remote": self.remote,
                        "branch": self.branch,
                    }
                    return result
                self._run(["git", "add", "-A", "--", "data"], cwd=checkout)
                staged = self._run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=checkout,
                    allowed={0, 1},
                )
                if staged.returncode == 0:
                    raise WudiTaskError(
                        "empty_transaction",
                        "The task operation reported a change but staged no data.",
                    )
                self._run(["git", "config", "user.name", actor.login], cwd=checkout)
                self._run(
                    [
                        "git",
                        "config",
                        "user.email",
                        f"{actor.github_id}+{actor.login}@users.noreply.github.com",
                    ],
                    cwd=checkout,
                )
                self._run(
                    ["git", "commit", "-m", commit_message(result)],
                    cwd=checkout,
                )
                commit = self._run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=checkout,
                ).stdout.strip()
                if self.before_push is not None:
                    self.before_push(attempt, checkout)
                push = self._push(checkout)
                if push.returncode == 0:
                    result["sync"] = {
                        "mode": "remote",
                        "confirmed": True,
                        "attempts": attempt,
                        "remote": self.remote,
                        "branch": self.branch,
                        "commit": commit,
                    }
                    return result
                combined = f"{push.stdout}\n{push.stderr}".strip()
                if self._is_non_fast_forward(combined):
                    last_rejection = combined
                    time.sleep(0.04 * attempt)
                    continue
                if self._remote_matches(result):
                    result["sync"] = {
                        "mode": "remote",
                        "confirmed": True,
                        "confirmation": "remote_reconciliation",
                        "attempts": attempt,
                        "remote": self.remote,
                        "branch": self.branch,
                        "commit": commit,
                    }
                    return result
                raise WudiTaskError(
                    "push_status_unknown",
                    "The ordinary push did not complete; do not start or finish task work yet.",
                    details={
                        "remote": self.remote,
                        "branch": self.branch,
                        "output": combined,
                        "task_id": result.get("task_id"),
                        "claim_token": (
                            result.get("task", {}).get("claim", {}).get("token")
                            if isinstance(result.get("task"), dict)
                            and isinstance(result.get("task", {}).get("claim"), dict)
                            else None
                        ),
                        "action": "Retry the same command to confirm remote state.",
                    },
                    exit_code=4,
                )
        raise WudiTaskError(
            "concurrent_update_exhausted",
            "The task hub kept changing and the update could not be confirmed.",
            details={
                "attempts": self.max_attempts,
                "last_rejection": last_rejection,
                "action": "Retry the command from a fresh remote snapshot.",
            },
            exit_code=3,
        )

    def _clone(self, checkout: Path) -> None:
        assert self.remote is not None
        assert self.branch is not None
        process = self._run(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                self.branch,
                self.remote,
                str(checkout),
            ],
            cwd=checkout.parent,
            allowed=None,
        )
        if process.returncode != 0:
            raise WudiTaskError(
                "remote_read_failed",
                "Could not clone the latest WudiTask state.",
                details={
                    "remote": self.remote,
                    "branch": self.branch,
                    "stderr": process.stderr.strip(),
                },
                exit_code=4,
            )

    def _push(self, checkout: Path) -> subprocess.CompletedProcess[str]:
        assert self.branch is not None
        return self._run(
            ["git", "push", "origin", f"HEAD:refs/heads/{self.branch}"],
            cwd=checkout,
            allowed=None,
        )

    def _remote_matches(self, result: dict[str, Any]) -> bool:
        expected = result.get("task")
        task_id = result.get("task_id")
        if not isinstance(expected, dict) or not isinstance(task_id, str):
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="wuditask-confirm-") as temporary:
                checkout = Path(temporary) / "hub"
                self._clone(checkout)
                record = TaskRepository(checkout).load_index().get(task_id)
                return record is not None and record.task == expected
        except WudiTaskError:
            return False

    @staticmethod
    def _is_non_fast_forward(output: str) -> bool:
        value = output.lower()
        return (
            "non-fast-forward" in value
            or "(fetch first)" in value
            or "(stale info)" in value
            or "failed to update ref" in value
            or ("cannot lock ref" in value and "expected" in value)
        )

    @staticmethod
    def _run(
        command: list[str],
        *,
        cwd: Path,
        allowed: set[int] | None = {0},
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if allowed is not None and process.returncode not in allowed:
            raise WudiTaskError(
                "git_command_failed",
                f"Git command failed: {' '.join(command)}",
                details={
                    "returncode": process.returncode,
                    "stdout": process.stdout.strip(),
                    "stderr": process.stderr.strip(),
                },
                exit_code=4,
            )
        return process

    @contextlib.contextmanager
    def _local_lock(self) -> Iterator[None]:
        assert self.root is not None
        lock_path = self.root / ".wuditask.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return
            import msvcrt  # pragma: no cover - Windows only

            handle.seek(0)
            if handle.read(1) == "":
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
