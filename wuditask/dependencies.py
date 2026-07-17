from __future__ import annotations

from typing import Any

from .errors import WudiTaskError
from .repository import TaskIndex, TaskRecord


def completion_is_ready(task: dict[str, Any]) -> tuple[bool, str]:
    completion = task.get("completion")
    if not isinstance(completion, dict):
        return False, "archived task has no completion record"
    if completion.get("outcome") != "done":
        return False, f"dependency outcome is {completion.get('outcome', 'unknown')}"
    evidence = completion.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False, "dependency has no completion evidence"
    if not all(isinstance(item, str) and item.strip() for item in evidence):
        return False, "dependency has invalid completion evidence"
    return True, "archived as done with evidence"


def _cycle_reachable(task_id: str, index: TaskIndex) -> list[str] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(current: str) -> list[str] | None:
        if current in active_set:
            start = active.index(current)
            return active[start:] + [current]
        if current in visited:
            return None
        record = index.get(current)
        if record is None:
            return None
        active.append(current)
        active_set.add(current)
        for dependency in record.task.get("dependencies", []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        active.pop()
        active_set.remove(current)
        visited.add(current)
        return None

    return visit(task_id)


def _expanded_dependency(dependency_id: str, index: TaskIndex) -> dict[str, Any]:
    record = index.get(dependency_id)
    if record is None:
        return {
            "id": dependency_id,
            "exists": False,
            "complete": False,
            "reason": "dependency task does not exist",
        }
    task = record.task
    completion = task.get("completion") if record.archived else None
    complete, reason = (
        completion_is_ready(task)
        if record.archived
        else (False, "dependency is still open")
    )
    return {
        "id": dependency_id,
        "exists": True,
        "location": "archive" if record.archived else "open",
        "complete": complete,
        "reason": reason,
        "repo": task["repo"],
        "source": task["source"],
        "active_agents": task["active_agents"],
        "outcome": completion.get("outcome") if isinstance(completion, dict) else None,
        "evidence": completion.get("evidence", []) if isinstance(completion, dict) else [],
    }


def task_dependency_report(record: TaskRecord, index: TaskIndex) -> dict[str, Any]:
    task = record.task
    dependencies = [
        _expanded_dependency(dependency_id, index)
        for dependency_id in task.get("dependencies", [])
    ]
    cycle = _cycle_reachable(task["id"], index)
    blockers = [
        {"id": dependency["id"], "reason": dependency["reason"]}
        for dependency in dependencies
        if not dependency["complete"]
    ]
    if cycle:
        blockers.append(
            {
                "id": task["id"],
                "reason": f"dependency cycle: {' -> '.join(cycle)}",
            }
        )
    ready = not blockers
    if record.archived:
        ready, _ = completion_is_ready(task)
        state = task["completion"]["outcome"]
    elif task["active_agents"]:
        state = "in_progress"
    elif ready:
        state = "ready"
    else:
        state = "blocked"
    return {
        "id": task["id"],
        "repo": task["repo"],
        "source": task["source"],
        "priority": task["priority"],
        "created_at": task["created_at"],
        "active_agents": task["active_agents"],
        "ready": ready,
        "state": state,
        "cycle": cycle,
        "blockers": blockers,
        "dependencies": dependencies,
    }


def dependency_report(index: TaskIndex, task_id: str | None = None) -> dict[str, Any]:
    if task_id is not None:
        record = index.get(task_id)
        if record is None:
            raise WudiTaskError(
                "task_not_found",
                f"Task {task_id} does not exist.",
                details={"task_id": task_id},
            )
        return {"task": task_dependency_report(record, index)}
    reports = [
        task_dependency_report(record, index)
        for record in sorted(
            index.open.values(),
            key=lambda item: (
                item.task["priority"],
                item.task["created_at"],
                item.task["id"],
            ),
        )
    ]
    return {
        "tasks": reports,
        "summary": {
            "open": len(reports),
            "ready": sum(item["state"] == "ready" for item in reports),
            "in_progress": sum(item["state"] == "in_progress" for item in reports),
            "blocked": sum(item["state"] == "blocked" for item in reports),
        },
    }
