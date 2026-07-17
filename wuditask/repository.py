from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import DataValidationError, WudiTaskError
from .model import SCHEMA_VERSION, require_valid_task, validate_task
from .util import atomic_write_json, read_json


HUB_SCHEMA_VERSION = 2
TOOL_API_VERSION = 2


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

    def _layout_issues(self) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        expected = (
            (self.manifest_path, "file"),
            (self.root / "data", "directory"),
            (self.open_dir, "directory"),
            (self.archive_dir, "directory"),
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

    def add(self, task: dict[str, Any]) -> Path:
        require_valid_task(task, archived=False)
        index = self.load_index()
        if index.get(task["id"]):
            raise WudiTaskError(
                "task_already_exists",
                f"Task {task['id']} already exists.",
                details={"task_id": task["id"]},
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
