from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from wuditask.model import Identity
from wuditask.repository import TaskRepository
from wuditask.workflow import create_task

ACTOR = Identity("alice", 1001)
OTHER_ACTOR = Identity("bob", 1002)


def spec(
    title: str = "Test task",
    *,
    repo: str = "acme/service",
    dependencies: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "repo": repo,
        "source": {
            "kind": "text",
            "reason": "Synthetic task used by the WudiTask test suite.",
        },
        "goal": f"Complete {title.lower()} with observable behavior.",
        "context": ["Keep the public API stable."],
        "acceptance_criteria": [
            {
                "description": f"{title} passes its regression check.",
                "verification": {
                    "type": "command",
                    "value": "python3 -m unittest",
                },
            }
        ],
        "dependencies": dependencies or [],
        "priority": "P2",
        "links": [],
    }


def make_repository(root: Path) -> TaskRepository:
    repository = TaskRepository(root)
    repository.initialize()
    return repository


def add_task(
    repository: TaskRepository,
    task_id: str,
    *,
    title: str = "Test task",
    repo: str = "acme/service",
    dependencies: list[str] | None = None,
) -> dict[str, Any]:
    return create_task(
        repository,
        spec(title, repo=repo, dependencies=dependencies),
        ACTOR,
        task_id=task_id,
        now="2026-07-11T12:00:00Z",
    )["task"]


def git(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *command],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def make_hub_origin(
    base: Path,
    *,
    name: str = "hub",
    branch: str = "main",
) -> Path:
    origin = base / f"{name}.git"
    git(["init", "--bare", f"--initial-branch={branch}", str(origin)], base)
    seed = base / f"{name}-seed"
    seed.mkdir()
    git(["init", "-b", branch], seed)
    git(["config", "user.name", "seed"], seed)
    git(["config", "user.email", "seed@example.invalid"], seed)
    make_repository(seed)
    git(["add", "hub.json", "data"], seed)
    git(["commit", "-m", "initialize task hub"], seed)
    git(["remote", "add", "origin", str(origin)], seed)
    git(["push", "-u", "origin", branch], seed)
    return origin
