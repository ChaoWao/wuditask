from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import time
import uuid
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


def default_cache_root(*, home: Path | None = None) -> Path:
    if home is not None:
        base = home.expanduser().resolve() / ".cache"
    else:
        configured = os.environ.get("XDG_CACHE_HOME", "").strip()
        candidate = Path(configured).expanduser() if configured else None
        base = (
            candidate
            if candidate is not None and candidate.is_absolute()
            else Path.home().expanduser().resolve() / ".cache"
        )
    return (base / "wuditask").resolve()


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
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
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _try_file_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        acquired = False
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        else:  # pragma: no cover - Windows only
            import msvcrt

            handle.seek(0)
            if handle.read(1) == "":
                handle.write("0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        try:
            yield acquired
        finally:
            if acquired:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                else:  # pragma: no cover - Windows only
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class GitCoordinator:
    """Run optimistic Git transactions without force-pushing."""

    def __init__(
        self,
        *,
        local_root: Path | None = None,
        remote: str | None = None,
        branch: str | None = None,
        cache_root: Path | None = None,
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
        self._cache_root = (
            (cache_root or default_cache_root()).expanduser().resolve()
            if remote is not None
            else None
        )
        self._cache_key = (
            hashlib.sha256(f"{self.remote}\0{self.branch}".encode("utf-8")).hexdigest()
            if remote is not None
            else None
        )
        if remote is not None:
            branch_check = subprocess.run(
                ["git", "check-ref-format", f"refs/heads/{self.branch}"],
                check=False,
                capture_output=True,
                text=True,
            )
            if branch_check.returncode != 0:
                raise WudiTaskError(
                    "invalid_git_coordinator",
                    "A remote task Hub branch must be a valid Git branch name.",
                    details={"branch": self.branch},
                )

    @property
    def distributed(self) -> bool:
        return self.remote is not None

    @property
    def cache_path(self) -> Path:
        if not self.distributed:
            raise WudiTaskError(
                "local_hub_has_no_cache",
                "An explicit local task Hub does not use the remote cache.",
            )
        assert self._cache_root is not None
        assert self._cache_key is not None
        return self._cache_root / "hubs" / f"{self._cache_key}.git"

    @property
    def _operations_root(self) -> Path:
        assert self._cache_root is not None
        return self._cache_root / "operations"

    @property
    def _cache_lock_path(self) -> Path:
        assert self._cache_root is not None
        assert self._cache_key is not None
        return self._cache_root / "locks" / f"{self._cache_key}.lock"

    def _operation_lease_path(self, operation: Path) -> Path:
        assert self._cache_root is not None
        return self._cache_root / "locks" / "operations" / f"{operation.name}.lock"

    @contextlib.contextmanager
    def snapshot(self) -> Iterator[TaskRepository]:
        if not self.distributed:
            assert self.root is not None
            repository = TaskRepository(self.root)
            yield repository
            return
        with self._remote_worktree("read") as checkout:
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
            with self._remote_worktree("write") as checkout:
                repository = TaskRepository(checkout)
                result = operation(repository)
                if not result.get("changed", True):
                    result["sync"] = {
                        "mode": "remote",
                        "confirmed": True,
                        "attempts": attempt,
                        "remote": self.remote,
                        "branch": self.branch,
                        "commit": self._run(
                            ["git", "rev-parse", "HEAD"],
                            cwd=checkout,
                        ).stdout.strip(),
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
                self._run(
                    [
                        "git",
                        "-c",
                        f"user.name={actor.login}",
                        "-c",
                        (
                            "user.email="
                            f"{actor.github_id}+{actor.login}@users.noreply.github.com"
                        ),
                        "commit",
                        "-m",
                        commit_message(result),
                    ],
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
                    confirmed_commit = result.pop(
                        "_remote_confirmation_commit",
                        commit,
                    )
                    result["sync"] = {
                        "mode": "remote",
                        "confirmed": True,
                        "confirmation": "remote_reconciliation",
                        "attempts": attempt,
                        "remote": self.remote,
                        "branch": self.branch,
                        "commit": confirmed_commit,
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
                        "task_ids": result.get("deleted_task_ids"),
                        "deletion_receipt": (
                            result.get("deletion_receipt", {}).get("id")
                            if isinstance(result.get("deletion_receipt"), dict)
                            else None
                        ),
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

    def _ensure_cache(self) -> None:
        assert self.remote is not None
        assert self._cache_key is not None
        cache_path = self.cache_path
        cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging_prefix = f".{self._cache_key}-"
        for stale in cache_path.parent.glob(f"{staging_prefix}*.tmp"):
            shutil.rmtree(stale, ignore_errors=True)
        if not cache_path.exists():
            staging = cache_path.parent / (
                f"{staging_prefix}{os.getpid()}-{uuid.uuid4().hex}.tmp"
            )
            try:
                initialized = self._run(
                    ["git", "init", "--bare", str(staging)],
                    cwd=cache_path.parent,
                    allowed=None,
                )
                if initialized.returncode != 0:
                    raise WudiTaskError(
                        "hub_cache_initialization_failed",
                        "Could not initialize the persistent task Hub cache.",
                        details={
                            "cache": str(cache_path),
                            "stderr": initialized.stderr.strip(),
                        },
                        exit_code=4,
                    )
                configured = self._run(
                    ["git", "remote", "add", "origin", self.remote],
                    cwd=staging,
                    allowed=None,
                )
                if configured.returncode != 0:
                    raise WudiTaskError(
                        "hub_cache_initialization_failed",
                        "Could not configure the persistent task Hub cache.",
                        details={
                            "cache": str(cache_path),
                            "stderr": configured.stderr.strip(),
                        },
                        exit_code=4,
                    )
                staging.replace(cache_path)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            return

        if not cache_path.is_dir():
            raise WudiTaskError(
                "hub_cache_invalid",
                "The persistent task Hub cache is not usable.",
                details={
                    "cache": str(cache_path),
                    "remote": self.remote,
                    "action": "Remove this disposable cache path and retry.",
                },
                exit_code=4,
            )
        bare = self._run(
            ["git", "rev-parse", "--is-bare-repository"],
            cwd=cache_path,
            allowed=None,
        )
        configured = self._run(
            ["git", "config", "--local", "--get", "remote.origin.url"],
            cwd=cache_path,
            allowed=None,
        )
        if (
            bare.returncode != 0
            or bare.stdout.strip() != "true"
            or configured.returncode != 0
            or configured.stdout.strip() != self.remote
        ):
            raise WudiTaskError(
                "hub_cache_invalid",
                "The persistent task Hub cache is not usable.",
                details={
                    "cache": str(cache_path),
                    "remote": self.remote,
                    "action": "Remove this disposable cache directory and retry.",
                },
                exit_code=4,
            )

    def _fetch_head(self) -> str:
        assert self.branch is not None
        remote_ref = f"refs/remotes/origin/{self.branch}"
        fetched = self._run(
            [
                "git",
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                "origin",
                f"+refs/heads/{self.branch}:{remote_ref}",
            ],
            cwd=self.cache_path,
            allowed=None,
        )
        if fetched.returncode != 0:
            raise WudiTaskError(
                "remote_read_failed",
                "Could not fetch the latest WudiTask state.",
                details={
                    "remote": self.remote,
                    "branch": self.branch,
                    "cache": str(self.cache_path),
                    "stderr": fetched.stderr.strip(),
                },
                exit_code=4,
            )
        resolved = self._run(
            ["git", "rev-parse", "--verify", f"{remote_ref}^{{commit}}"],
            cwd=self.cache_path,
            allowed=None,
        )
        if resolved.returncode != 0:
            raise WudiTaskError(
                "remote_read_failed",
                "Could not resolve the configured task Hub branch.",
                details={
                    "remote": self.remote,
                    "branch": self.branch,
                    "cache": str(self.cache_path),
                    "stderr": resolved.stderr.strip(),
                },
                exit_code=4,
            )
        return resolved.stdout.strip()

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return
        shutil.rmtree(path, ignore_errors=False)

    def _remove_operation(self, operation: Path) -> None:
        if operation.is_symlink() or not operation.is_dir():
            self._remove_path(operation)
            return
        checkout = operation / "hub"
        if checkout.exists() or checkout.is_symlink():
            removed = self._run(
                ["git", "worktree", "remove", "--force", str(checkout)],
                cwd=self.cache_path,
                allowed=None,
            )
            if removed.returncode != 0:
                self._remove_path(checkout)
        self._remove_path(operation)

    def _reap_orphan_operations(self) -> None:
        assert self._cache_key is not None
        prefix = f"{self._cache_key}-"
        leases_root = self._cache_lock_path.parent / "operations"
        if self._operations_root.is_dir():
            for operation in sorted(self._operations_root.iterdir()):
                if not operation.name.startswith(prefix):
                    continue
                lease_path = self._operation_lease_path(operation)
                with _try_file_lock(lease_path) as acquired:
                    if not acquired:
                        continue
                    self._remove_operation(operation)
                lease_path.unlink(missing_ok=True)
        self._run(
            ["git", "worktree", "prune", "--expire", "now"],
            cwd=self.cache_path,
        )
        if leases_root.is_dir():
            for lease_path in sorted(leases_root.glob(f"{prefix}*.lock")):
                operation_name = lease_path.name.removesuffix(".lock")
                operation = self._operations_root / operation_name
                if operation.exists() or operation.is_symlink():
                    continue
                with _try_file_lock(lease_path) as acquired:
                    if not acquired:
                        continue
                lease_path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _remote_worktree(self, purpose: str) -> Iterator[Path]:
        manager = self._remote_worktree_impl(purpose)
        try:
            checkout = manager.__enter__()
        except WudiTaskError:
            raise
        except OSError as exc:
            raise self._cache_io_failure(exc) from exc
        try:
            yield checkout
        except BaseException as exc:
            try:
                suppressed = manager.__exit__(type(exc), exc, exc.__traceback__)
            except WudiTaskError:
                raise
            except OSError as cleanup_error:
                raise self._cache_io_failure(cleanup_error) from cleanup_error
            if not suppressed:
                raise
        else:
            try:
                manager.__exit__(None, None, None)
            except WudiTaskError:
                raise
            except OSError as exc:
                raise self._cache_io_failure(exc) from exc

    def _cache_io_failure(self, error: OSError) -> WudiTaskError:
        assert self._cache_root is not None
        return WudiTaskError(
            "hub_cache_io_failed",
            "The persistent task Hub cache could not be accessed.",
            details={
                "cache_root": str(self._cache_root),
                "path": str(error.filename) if error.filename else None,
                "error": str(error),
                "action": "Check the cache path, permissions, and available space.",
            },
            exit_code=4,
        )

    @contextlib.contextmanager
    def _remote_worktree_impl(self, purpose: str) -> Iterator[Path]:
        assert self._cache_key is not None
        operation = self._operations_root / (
            f"{self._cache_key}-{purpose}-{os.getpid()}-{uuid.uuid4().hex}"
        )
        checkout = operation / "hub"
        lease_path = self._operation_lease_path(operation)
        assert self._cache_root is not None
        self._cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with _file_lock(lease_path):
                try:
                    with _file_lock(self._cache_lock_path):
                        self._operations_root.mkdir(
                            mode=0o700,
                            parents=True,
                            exist_ok=True,
                        )
                        self._ensure_cache()
                        self._reap_orphan_operations()
                        commit = self._fetch_head()
                        operation.mkdir(mode=0o700)
                        added = self._run(
                            [
                                "git",
                                "worktree",
                                "add",
                                "--quiet",
                                "--detach",
                                str(checkout),
                                commit,
                            ],
                            cwd=self.cache_path,
                            allowed=None,
                        )
                        if added.returncode != 0:
                            raise WudiTaskError(
                                "hub_cache_worktree_failed",
                                "Could not create an isolated task Hub worktree.",
                                details={
                                    "cache": str(self.cache_path),
                                    "stderr": added.stderr.strip(),
                                },
                                exit_code=4,
                            )
                    yield checkout
                finally:
                    with _file_lock(self._cache_lock_path):
                        if operation.exists() or operation.is_symlink():
                            if self.cache_path.is_dir():
                                self._remove_operation(operation)
                            else:
                                self._remove_path(operation)
                        if self.cache_path.is_dir():
                            self._run(
                                ["git", "worktree", "prune", "--expire", "now"],
                                cwd=self.cache_path,
                                allowed=None,
                            )
        finally:
            try:
                lease_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _push(self, checkout: Path) -> subprocess.CompletedProcess[str]:
        assert self.branch is not None
        assert self.remote is not None
        return self._run(
            [
                "git",
                "-c",
                "remote.origin.mirror=false",
                "-c",
                "push.followTags=false",
                "push",
                "--no-force",
                "--no-force-with-lease",
                "--no-mirror",
                "--no-follow-tags",
                "--",
                self.remote,
                f"HEAD:refs/heads/{self.branch}",
            ],
            cwd=checkout,
            allowed=None,
        )

    def _remote_matches(self, result: dict[str, Any]) -> bool:
        expected = result.get("task")
        task_id = result.get("task_id")
        deleted_task_ids = result.get("deleted_task_ids")
        deletion_receipt = result.get("deletion_receipt")
        expects_deletion = (
            isinstance(deleted_task_ids, list)
            and bool(deleted_task_ids)
            and all(isinstance(value, str) for value in deleted_task_ids)
            and isinstance(deletion_receipt, dict)
            and isinstance(deletion_receipt.get("id"), str)
        )
        expects_task = isinstance(expected, dict) and isinstance(task_id, str)
        if not expects_task and not expects_deletion:
            return False
        try:
            with self._remote_worktree("confirm") as checkout:
                index = TaskRepository(checkout).load_index()
                if expects_deletion:
                    repository = TaskRepository(checkout)
                    receipts = repository.load_deletion_receipts()
                    remote_receipt = receipts.get(deletion_receipt["id"])
                    if remote_receipt is None or not all(
                        index.get(value) is None for value in deleted_task_ids
                    ):
                        return False
                    result["deletion_receipt"] = remote_receipt
                    result["deleted_by"] = remote_receipt["deleted_by"]
                    result["reason"] = remote_receipt["reason"]
                    result["_remote_confirmation_commit"] = self._run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=checkout,
                    ).stdout.strip()
                    return True
                record = index.get(task_id)
                if record is None or record.task != expected:
                    return False
                result["_remote_confirmation_commit"] = self._run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=checkout,
                ).stdout.strip()
                return True
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
        with _file_lock(self.root / ".wuditask.lock"):
            yield
