from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import DataValidationError, WudiTaskError
from .model import SCHEMA_VERSION, require_valid_task, validate_task
from .util import (
    DELETION_RECEIPT_ID_RE,
    TASK_ID_RE,
    atomic_write_json,
    deletion_receipt_id,
    is_utc_timestamp,
    read_json,
)


HUB_SCHEMA_VERSION = 3
TOOL_API_VERSION = 5


@dataclass(frozen=True)
class TaskRecord:
    task: dict[str, Any]
    path: Path
    archived: bool


@dataclass
class TaskIndex:
    open: dict[str, TaskRecord]
    archived: dict[str, TaskRecord]

    @property
    def all(self) -> dict[str, TaskRecord]:
        return {**self.archived, **self.open}

    def get(self, task_id: str) -> TaskRecord | None:
        return self.open.get(task_id) or self.archived.get(task_id)


class TaskRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "hub.json"
        self.open_dir = self.root / "data" / "open"
        self.archive_dir = self.root / "data" / "archive"
        self.deletions_dir = self.root / "data" / "deletions"

    def initialize(self) -> None:
        self._require_safe_layout()
        if not self.manifest_path.exists():
            atomic_write_json(
                self.manifest_path,
                {
                    "schema_version": HUB_SCHEMA_VERSION,
                    "tool_api_version": TOOL_API_VERSION,
                },
            )
        issues = self._manifest_issues()
        if issues:
            raise DataValidationError(issues)
        self.ensure_layout()

    def ensure_layout(self) -> None:
        self._require_safe_layout()
        self.open_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.deletions_dir.mkdir(parents=True, exist_ok=True)

    def _layout_issues(self) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = (
            (self.manifest_path, "file"),
            (self.root / "data", "directory"),
            (self.open_dir, "directory"),
            (self.archive_dir, "directory"),
            (self.deletions_dir, "directory"),
        )
        for path, kind in expected:
            relative = path.relative_to(self.root).as_posix()
            if path.is_symlink():
                issues.append({"path": relative, "message": "must not be a symlink"})
                continue
            if not path.exists():
                continue
            matches = path.is_file() if kind == "file" else path.is_dir()
            if not matches:
                issues.append({"path": relative, "message": f"must be a {kind}"})
        data_dir = self.root / "data"
        if data_dir.is_dir() and not data_dir.is_symlink():
            for path in data_dir.rglob("*"):
                if path.is_symlink():
                    issues.append(
                        {
                            "path": path.relative_to(self.root).as_posix(),
                            "message": "must not be a symlink",
                        }
                    )
        return issues

    def _require_safe_layout(self) -> None:
        issues = self._layout_issues()
        if issues:
            raise DataValidationError(issues)

    def _manifest_issues(self) -> list[dict[str, str]]:
        relative = "hub.json"
        try:
            manifest = read_json(self.manifest_path)
        except WudiTaskError as exc:
            return [{"path": relative, "message": exc.message}]
        expected_keys = {"schema_version", "tool_api_version"}
        if not isinstance(manifest, dict):
            return [{"path": relative, "message": "must be a JSON object"}]
        issues: list[dict[str, str]] = []
        if set(manifest) != expected_keys:
            issues.append(
                {
                    "path": relative,
                    "message": "must contain only schema_version and tool_api_version",
                }
            )
        if manifest.get("schema_version") != HUB_SCHEMA_VERSION:
            issues.append(
                {
                    "path": f"{relative}:$.schema_version",
                    "message": f"must equal {HUB_SCHEMA_VERSION}",
                }
            )
        if manifest.get("tool_api_version") != TOOL_API_VERSION:
            issues.append(
                {
                    "path": f"{relative}:$.tool_api_version",
                    "message": f"must equal {TOOL_API_VERSION}",
                }
            )
        if HUB_SCHEMA_VERSION != SCHEMA_VERSION:
            issues.append(
                {
                    "path": relative,
                    "message": "tool task schema and Hub schema constants disagree",
                }
            )
        return issues

    def _files(self, directory: Path, recursive: bool) -> Iterable[Path]:
        if not directory.exists():
            return []
        iterator = directory.rglob("*.json") if recursive else directory.glob("*.json")
        return sorted(path for path in iterator if path.is_file())

    def validation_issues(self) -> list[dict[str, str]]:
        issues = self._layout_issues()
        if issues:
            return issues
        issues = self._manifest_issues()
        seen: dict[str, Path] = {}
        locations = (
            (self._files(self.open_dir, False), False),
            (self._files(self.archive_dir, True), True),
        )
        for files, archived in locations:
            for path in files:
                relative = path.relative_to(self.root).as_posix()
                try:
                    task = read_json(path)
                except WudiTaskError as exc:
                    issues.append({"path": relative, "message": exc.message})
                    continue
                for issue in validate_task(task, archived=archived):
                    issues.append(
                        {
                            "path": f"{relative}:{issue['path']}",
                            "message": issue["message"],
                        }
                    )
                if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                    continue
                task_id = task["id"]
                if path.stem != task_id:
                    issues.append(
                        {
                            "path": relative,
                            "message": f"filename must be {task_id}.json",
                        }
                    )
                if task_id in seen:
                    issues.append(
                        {
                            "path": relative,
                            "message": f"duplicates task ID already stored at {seen[task_id]}",
                        }
                    )
                else:
                    seen[task_id] = path
                if archived and isinstance(task.get("completion"), dict):
                    completed_at = task["completion"].get("completed_at")
                    if isinstance(completed_at, str) and len(completed_at) >= 4:
                        expected_year = completed_at[:4]
                        try:
                            actual_year = path.relative_to(self.archive_dir).parts[0]
                        except (ValueError, IndexError):
                            actual_year = ""
                        if actual_year != expected_year:
                            issues.append(
                                {
                                    "path": relative,
                                    "message": f"archived task must be under {expected_year}/",
                                }
                            )
        deleted_task_ids: dict[str, Path] = {}
        for path in self._files(self.deletions_dir, False):
            relative = path.relative_to(self.root).as_posix()
            try:
                receipt = read_json(path)
            except WudiTaskError as exc:
                issues.append({"path": relative, "message": exc.message})
                continue
            receipt_issues = self._deletion_receipt_issues(receipt)
            for issue in receipt_issues:
                issues.append(
                    {
                        "path": f"{relative}:{issue['path']}",
                        "message": issue["message"],
                    }
                )
            if not isinstance(receipt, dict):
                continue
            receipt_id = receipt.get("id")
            if isinstance(receipt_id, str) and path.stem != receipt_id:
                issues.append(
                    {
                        "path": relative,
                        "message": f"filename must be {receipt_id}.json",
                    }
                )
            task_ids = receipt.get("task_ids")
            if not isinstance(task_ids, list):
                continue
            for task_id in task_ids:
                if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
                    continue
                if task_id in seen:
                    issues.append(
                        {
                            "path": relative,
                            "message": (
                                f"deleted task ID still exists at {seen[task_id]}"
                            ),
                        }
                    )
                previous = deleted_task_ids.get(task_id)
                if previous is not None:
                    issues.append(
                        {
                            "path": relative,
                            "message": (
                                "task ID already appears in deletion receipt "
                                f"{previous.relative_to(self.root).as_posix()}"
                            ),
                        }
                    )
                else:
                    deleted_task_ids[task_id] = path
        return issues

    @staticmethod
    def _deletion_receipt_issues(receipt: object) -> list[dict[str, str]]:
        if not isinstance(receipt, dict):
            return [{"path": "$", "message": "must be a JSON object"}]
        required = {
            "receipt_version",
            "id",
            "task_ids",
            "reason",
            "deleted_by",
            "deleted_at",
        }
        issues: list[dict[str, str]] = []
        if set(receipt) != required:
            issues.append(
                {
                    "path": "$",
                    "message": "must contain only the deletion receipt fields",
                }
            )
        if receipt.get("receipt_version") != 2:
            issues.append({"path": "$.receipt_version", "message": "must equal 2"})
        receipt_id = receipt.get("id")
        if not isinstance(receipt_id, str) or not DELETION_RECEIPT_ID_RE.fullmatch(
            receipt_id
        ):
            issues.append(
                {
                    "path": "$.id",
                    "message": "must match WDR followed by 24 hexadecimal characters",
                }
            )
        task_ids = receipt.get("task_ids")
        valid_task_ids = (
            isinstance(task_ids, list)
            and bool(task_ids)
            and all(
                isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id)
                for task_id in task_ids
            )
        )
        if not valid_task_ids:
            issues.append(
                {
                    "path": "$.task_ids",
                    "message": "must be a non-empty array of task IDs",
                }
            )
        elif task_ids != sorted(set(task_ids)):
            issues.append(
                {
                    "path": "$.task_ids",
                    "message": "must be unique and sorted",
                }
            )
        reason = receipt.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
        ):
            issues.append(
                {"path": "$.reason", "message": "must be non-empty and trimmed"}
            )
        deleted_by = receipt.get("deleted_by")
        valid_actor = (
            isinstance(deleted_by, str)
            and bool(deleted_by.strip())
            and deleted_by == deleted_by.strip()
        )
        if not valid_actor:
            issues.append(
                {
                    "path": "$.deleted_by",
                    "message": "must be a non-empty trimmed GitHub login",
                }
            )
        if not is_utc_timestamp(receipt.get("deleted_at")):
            issues.append(
                {
                    "path": "$.deleted_at",
                    "message": "must be a valid UTC timestamp ending in Z",
                }
            )
        if (
            isinstance(receipt_id, str)
            and valid_task_ids
            and isinstance(reason, str)
            and reason.strip()
            and valid_actor
            and receipt_id
            != deletion_receipt_id(task_ids, reason, deleted_by)
        ):
            issues.append(
                {
                    "path": "$.id",
                    "message": "does not match task_ids, reason, and deleted_by",
                }
            )
        return issues

    def load_index(self) -> TaskIndex:
        issues = self.validation_issues()
        if issues:
            raise DataValidationError(issues)
        open_tasks: dict[str, TaskRecord] = {}
        archived_tasks: dict[str, TaskRecord] = {}
        for path in self._files(self.open_dir, False):
            task = read_json(path)
            open_tasks[task["id"]] = TaskRecord(task=task, path=path, archived=False)
        for path in self._files(self.archive_dir, True):
            task = read_json(path)
            archived_tasks[task["id"]] = TaskRecord(task=task, path=path, archived=True)
        return TaskIndex(open=open_tasks, archived=archived_tasks)

    def load_deletion_receipts(self) -> dict[str, dict[str, Any]]:
        issues = self.validation_issues()
        if issues:
            raise DataValidationError(issues)
        return {
            receipt["id"]: receipt
            for path in self._files(self.deletions_dir, False)
            for receipt in [read_json(path)]
        }

    def deletion_receipt_for_task(self, task_id: str) -> dict[str, Any] | None:
        for receipt in self.load_deletion_receipts().values():
            if task_id in receipt["task_ids"]:
                return receipt
        return None

    def add(self, task: dict[str, Any]) -> Path:
        require_valid_task(task, archived=False)
        index = self.load_index()
        if index.get(task["id"]):
            raise WudiTaskError(
                "task_already_exists",
                f"Task {task['id']} already exists.",
                details={"task_id": task["id"]},
            )
        receipt = self.deletion_receipt_for_task(task["id"])
        if receipt is not None:
            raise WudiTaskError(
                "task_id_deleted",
                f"Task ID {task['id']} was permanently reserved by a deletion receipt.",
                details={"task_id": task["id"], "deletion_receipt": receipt["id"]},
                exit_code=3,
            )
        path = self.open_dir / f"{task['id']}.json"
        atomic_write_json(path, task)
        return path

    def write_open(self, task: dict[str, Any]) -> Path:
        self._require_safe_layout()
        require_valid_task(task, archived=False)
        path = self.open_dir / f"{task['id']}.json"
        if not path.exists():
            raise WudiTaskError(
                "task_not_open",
                f"Task {task['id']} is not open.",
                details={"task_id": task["id"]},
            )
        atomic_write_json(path, task)
        return path

    def archive(self, task: dict[str, Any]) -> Path:
        self._require_safe_layout()
        require_valid_task(task, archived=True)
        source = self.open_dir / f"{task['id']}.json"
        if not source.exists():
            raise WudiTaskError(
                "task_not_open",
                f"Task {task['id']} is not open.",
                details={"task_id": task["id"]},
            )
        year = task["completion"]["completed_at"][:4]
        destination = self.archive_dir / year / f"{task['id']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(source, task)
        os.replace(source, destination)
        return destination

    def delete_archived(
        self,
        task_ids: Iterable[str],
        receipt: dict[str, Any],
    ) -> list[Path]:
        """Delete one prevalidated batch of archived task records."""

        self._require_safe_layout()
        index = self.load_index()
        canonical_task_ids = sorted(task_ids)
        paths: list[Path] = []
        for task_id in canonical_task_ids:
            record = index.archived.get(task_id)
            if record is None:
                location = "open" if task_id in index.open else "missing"
                raise WudiTaskError(
                    "archived_tasks_required",
                    "Delete accepts only existing archived tasks.",
                    details={"targets": [{"task_id": task_id, "location": location}]},
                    exit_code=3,
                )
            paths.append(record.path)

        receipt_issues = self._deletion_receipt_issues(receipt)
        if receipt_issues:
            raise DataValidationError(receipt_issues)
        if receipt["task_ids"] != canonical_task_ids:
            raise WudiTaskError(
                "deletion_receipt_mismatch",
                "The deletion receipt must cover exactly the archived task batch.",
                details={
                    "task_ids": canonical_task_ids,
                    "receipt_task_ids": receipt["task_ids"],
                },
                exit_code=3,
            )
        receipt_path = self.deletions_dir / f"{receipt['id']}.json"
        if receipt_path.exists():
            raise WudiTaskError(
                "deletion_receipt_conflict",
                f"Deletion receipt {receipt['id']} already exists.",
                details={"receipt_id": receipt["id"]},
                exit_code=3,
            )

        try:
            backups = {path: path.read_bytes() for path in paths}
        except OSError as exc:
            raise WudiTaskError(
                "archive_delete_failed",
                "Could not prepare the archived task batch for deletion.",
                details={"error": str(exc)},
                exit_code=4,
            ) from exc

        receipt_written = False
        try:
            atomic_write_json(receipt_path, receipt)
            receipt_written = True
            for path in paths:
                path.unlink()
        except BaseException as exc:
            restore_failures: list[dict[str, str]] = []
            for path, content in backups.items():
                if path.exists():
                    continue
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                except OSError as restore_error:
                    restore_failures.append(
                        {"path": str(path), "error": str(restore_error)}
                    )
            if receipt_written and not restore_failures:
                try:
                    receipt_path.unlink()
                except OSError as restore_error:
                    restore_failures.append(
                        {"path": str(receipt_path), "error": str(restore_error)}
                    )
            if isinstance(exc, OSError) or restore_failures:
                raise WudiTaskError(
                    "archive_delete_failed",
                    "Could not delete the complete archived task batch.",
                    details={
                        "error": str(exc),
                        "restore_failures": restore_failures,
                    },
                    exit_code=4,
                ) from exc
            raise

        for parent in sorted({path.parent for path in paths}, reverse=True):
            if parent == self.archive_dir:
                continue
            try:
                parent.rmdir()
            except OSError:
                pass
        return paths
