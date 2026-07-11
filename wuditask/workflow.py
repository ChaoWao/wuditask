from __future__ import annotations

from copy import deepcopy
from typing import Any

from .dependencies import dependency_report, task_dependency_report
from .errors import WudiTaskError
from .model import Identity, OUTCOMES, PRIORITIES, identity_matches, require_valid_task
from .repository import TaskRepository
from .util import new_claim_token, new_task_id, normalize_repo, utc_now


def _spec_missing(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    questions: list[str] = []
    checks = (
        ("title", "What concise title should identify this task?"),
        ("repo", "Which GitHub repository (owner/name) contains the work?"),
        ("goal", "What concrete outcome should this task achieve?"),
    )
    for field, question in checks:
        value = spec.get(field)
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
            questions.append(question)
    criteria = spec.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        missing.append("acceptance_criteria")
        questions.append("What observable checks prove this task is complete?")
    else:
        for index, criterion in enumerate(criteria):
            if isinstance(criterion, str):
                description = criterion
                verification = {"type": "manual", "value": criterion}
            elif isinstance(criterion, dict):
                description = criterion.get("description")
                verification = criterion.get("verification")
            else:
                description = None
                verification = None
            if not isinstance(description, str) or not description.strip():
                missing.append(f"acceptance_criteria[{index}].description")
                questions.append(
                    f"What observable result defines criterion {index + 1}?"
                )
            if isinstance(criterion, dict) and (
                not isinstance(verification, dict)
                or not isinstance(verification.get("type"), str)
                or not verification.get("type", "").strip()
                or not isinstance(verification.get("value"), str)
                or not verification.get("value", "").strip()
            ):
                missing.append(f"acceptance_criteria[{index}].verification")
                questions.append(f"How should criterion {index + 1} be verified?")
    return missing, questions


def create_task(
    repository: TaskRepository,
    spec: dict[str, Any],
    actor: Identity,
    *,
    task_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    missing, questions = _spec_missing(spec)
    if missing:
        raise WudiTaskError(
            "insufficient_task_spec",
            "The task needs more information before it can be added.",
            details={"missing": missing, "questions": questions},
        )
    priority = spec.get("priority", "P2")
    if priority not in PRIORITIES:
        raise WudiTaskError(
            "invalid_priority",
            "Priority must be P0, P1, P2, or P3.",
            details={"value": priority},
        )
    criteria: list[dict[str, Any]] = []
    for index, criterion in enumerate(spec["acceptance_criteria"], start=1):
        if isinstance(criterion, str):
            description = criterion
            verification = {"type": "manual", "value": criterion}
        elif isinstance(criterion, dict):
            description = criterion.get("description")
            verification = criterion.get("verification") or {
                "type": "manual",
                "value": description,
            }
        else:
            description = None
            verification = None
        criteria.append(
            {
                "id": f"AC-{index}",
                "description": description,
                "verification": verification,
            }
        )
    timestamp = now or utc_now()
    task = {
        "schema_version": 1,
        "id": task_id or new_task_id(timestamp),
        "title": spec["title"].strip(),
        "repo": normalize_repo(spec["repo"]),
        "created_by": actor.as_dict(),
        "owner": None,
        "priority": priority,
        "created_at": timestamp,
        "goal": spec["goal"].strip(),
        "context": list(spec.get("context") or []),
        "acceptance_criteria": criteria,
        "dependencies": list(dict.fromkeys(spec.get("dependencies") or [])),
        "claim": None,
        "links": list(spec.get("links") or []),
    }
    require_valid_task(task, archived=False)
    index = repository.load_index()
    existing = index.get(task["id"])
    if existing is not None:
        if not existing.archived and existing.task == task:
            return {
                "task": existing.task,
                "task_id": task["id"],
                "already_added": True,
                "changed": False,
                "message": f"{task['id']} is already present with the same specification.",
            }
        raise WudiTaskError(
            "task_id_conflict",
            f"Task ID {task['id']} already exists with different data.",
            details={
                "task_id": task["id"],
                "location": "archive" if existing.archived else "open",
            },
            exit_code=3,
        )
    missing_dependencies = [
        dependency
        for dependency in task["dependencies"]
        if index.get(dependency) is None
    ]
    if missing_dependencies:
        raise WudiTaskError(
            "missing_dependency",
            "Every dependency must already exist in WudiTask.",
            details={
                "task_id": task["id"],
                "missing": missing_dependencies,
                "question": "Add the dependency tasks first, or provide valid task IDs.",
            },
        )
    repository.add(task)
    return {
        "task": task,
        "task_id": task["id"],
        "already_added": False,
        "changed": True,
        "message": f"Added {task['id']}: {task['title']}",
    }


def claim_task(
    repository: TaskRepository,
    actor: Identity,
    *,
    repo: str | None = None,
    task_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    index = repository.load_index()
    normalized_repo = normalize_repo(repo) if repo else None
    if task_id:
        archived = index.archived.get(task_id)
        if archived:
            raise WudiTaskError(
                "task_already_archived",
                f"Task {task_id} has already been archived.",
                details={"task_id": task_id},
                exit_code=3,
            )
        record = index.open.get(task_id)
        if record is None:
            raise WudiTaskError(
                "task_not_found",
                f"Task {task_id} does not exist.",
                details={"task_id": task_id},
            )
        if normalized_repo and record.task["repo"] != normalized_repo:
            raise WudiTaskError(
                "repository_mismatch",
                f"Task {task_id} belongs to {record.task['repo']}, not {normalized_repo}.",
                details={"task_id": task_id, "task_repo": record.task["repo"]},
            )
        if record.task.get("claim") is not None:
            if identity_matches(record.task.get("owner"), actor):
                return {
                    "task": record.task,
                    "task_id": task_id,
                    "confirmed": True,
                    "already_claimed": True,
                    "changed": False,
                    "dependency_check": dependency_report(index, task_id)["task"],
                    "message": f"{task_id} is already claimed by {actor.login}.",
                }
            raise WudiTaskError(
                "claim_conflict",
                f"Task {task_id} is already claimed.",
                details={"task_id": task_id, "owner": record.task.get("owner")},
                exit_code=3,
            )
        candidates = [record]
    else:
        if normalized_repo is None:
            raise WudiTaskError(
                "repository_required",
                "A repository is required when no task ID is provided.",
                details={
                    "question": "Which current GitHub repository should supply the task?"
                },
            )
        candidates = [
            record
            for record in index.open.values()
            if record.task["repo"] == normalized_repo
            and record.task.get("claim") is None
        ]
        candidates.sort(
            key=lambda item: (
                item.task["priority"],
                item.task["created_at"],
                item.task["id"],
            )
        )
    blocked: list[dict[str, Any]] = []
    selected = None
    for candidate in candidates:
        report = task_dependency_report(candidate, index)
        if report["ready"]:
            selected = candidate
            break
        blocked.append(
            {"task_id": candidate.task["id"], "blockers": report["blockers"]}
        )
    if selected is None:
        raise WudiTaskError(
            "no_ready_task",
            "No unclaimed task with satisfied dependencies is available.",
            details={"repo": normalized_repo, "blocked": blocked},
            exit_code=3,
        )
    task = deepcopy(selected.task)
    claimed_at = now or utc_now()
    task["owner"] = actor.as_dict()
    task["claim"] = {
        "token": new_claim_token(),
        "github_login": actor.login,
        "github_id": actor.github_id,
        "claimed_at": claimed_at,
    }
    repository.write_open(task)
    return {
        "task": task,
        "task_id": task["id"],
        "confirmed": True,
        "already_claimed": False,
        "changed": True,
        "dependency_check": dependency_report(repository.load_index(), task["id"])[
            "task"
        ],
        "message": f"Claimed {task['id']} for {actor.login}.",
    }


def archive_task(
    repository: TaskRepository,
    actor: Identity,
    task_id: str,
    *,
    outcome: str,
    result: str | None,
    evidence: dict[str, str],
    now: str | None = None,
) -> dict[str, Any]:
    index = repository.load_index()
    existing_archive = index.archived.get(task_id)
    if existing_archive is not None:
        completion = existing_archive.task["completion"]
        existing_evidence = {
            item["criterion_id"]: item["evidence"]
            for item in completion.get("acceptance_results", [])
            if isinstance(item, dict)
        }
        same_request = (
            identity_matches(completion.get("completed_by"), actor)
            and completion.get("outcome") == outcome
            and isinstance(result, str)
            and completion.get("result") == result.strip()
            and (
                outcome != "done"
                or existing_evidence
                == {key: value.strip() for key, value in evidence.items()}
            )
        )
        if same_request:
            return {
                "task": existing_archive.task,
                "task_id": task_id,
                "confirmed": True,
                "already_archived": True,
                "changed": False,
                "message": f"{task_id} is already archived.",
            }
        raise WudiTaskError(
            "task_already_archived",
            f"Task {task_id} has already been archived.",
            details={"task_id": task_id, "completion": completion},
            exit_code=3,
        )
    record = index.open.get(task_id)
    if record is None:
        raise WudiTaskError(
            "task_not_found",
            f"Task {task_id} does not exist.",
            details={"task_id": task_id},
        )
    task = deepcopy(record.task)
    if task.get("claim") is None:
        raise WudiTaskError(
            "task_not_claimed",
            f"Task {task_id} must be claimed before it can be archived.",
            details={"task_id": task_id},
            exit_code=3,
        )
    if not identity_matches(task.get("owner"), actor):
        raise WudiTaskError(
            "owner_mismatch",
            f"Task {task_id} is owned by another GitHub user.",
            details={"task_id": task_id, "owner": task.get("owner")},
            exit_code=3,
        )
    if outcome not in OUTCOMES:
        raise WudiTaskError(
            "invalid_outcome",
            "Outcome must be done, failed, or cancelled.",
            details={"value": outcome},
        )
    if not isinstance(result, str) or not result.strip():
        raise WudiTaskError(
            "archive_result_required",
            "Archiving requires a concise result or reason.",
            details={"question": "What was completed, failed, or cancelled?"},
        )
    dep_report = task_dependency_report(record, index)
    if outcome == "done" and not dep_report["ready"]:
        raise WudiTaskError(
            "dependency_blocked",
            f"Task {task_id} cannot complete while dependencies are blocked.",
            details={"task_id": task_id, "blockers": dep_report["blockers"]},
            exit_code=3,
        )
    criterion_ids = [criterion["id"] for criterion in task["acceptance_criteria"]]
    unknown = sorted(set(evidence) - set(criterion_ids))
    if unknown:
        raise WudiTaskError(
            "unknown_acceptance_criterion",
            "Evidence refers to unknown acceptance criteria.",
            details={"unknown": unknown, "expected": criterion_ids},
        )
    if outcome == "done":
        missing = [
            criterion_id
            for criterion_id in criterion_ids
            if not evidence.get(criterion_id, "").strip()
        ]
        if missing:
            questions = [
                f"What evidence proves acceptance criterion {criterion_id} passed?"
                for criterion_id in missing
            ]
            raise WudiTaskError(
                "insufficient_archive_evidence",
                "Every acceptance criterion needs passing evidence.",
                details={"missing": missing, "questions": questions},
            )
        acceptance_results = [
            {
                "criterion_id": criterion_id,
                "status": "passed",
                "evidence": evidence[criterion_id].strip(),
            }
            for criterion_id in criterion_ids
        ]
    else:
        acceptance_results = [
            {
                "criterion_id": criterion_id,
                "status": "failed" if criterion_id in evidence else "skipped",
                "evidence": evidence.get(criterion_id, result).strip(),
            }
            for criterion_id in criterion_ids
        ]
    completed_at = now or utc_now()
    task["completion"] = {
        "outcome": outcome,
        "completed_at": completed_at,
        "completed_by": actor.as_dict(),
        "result": result.strip(),
        "acceptance_results": acceptance_results,
    }
    repository.archive(task)
    return {
        "task": task,
        "task_id": task_id,
        "confirmed": True,
        "already_archived": False,
        "changed": True,
        "message": f"Archived {task_id} with outcome {outcome}.",
    }


def release_task(
    repository: TaskRepository,
    actor: Identity,
    task_id: str,
    *,
    reason: str | None,
) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise WudiTaskError(
            "release_reason_required",
            "Releasing a task requires a reason.",
            details={"question": "Why is this task being returned to the queue?"},
        )
    index = repository.load_index()
    record = index.open.get(task_id)
    if record is None:
        raise WudiTaskError(
            "task_not_open",
            f"Task {task_id} is not open.",
            details={"task_id": task_id},
        )
    if record.task.get("claim") is None:
        return {
            "task": record.task,
            "task_id": task_id,
            "changed": False,
            "message": f"{task_id} is already unclaimed.",
        }
    if not identity_matches(record.task.get("owner"), actor):
        raise WudiTaskError(
            "owner_mismatch",
            f"Task {task_id} is owned by another GitHub user.",
            details={"task_id": task_id, "owner": record.task.get("owner")},
            exit_code=3,
        )
    task = deepcopy(record.task)
    task["owner"] = None
    task["claim"] = None
    repository.write_open(task)
    return {
        "task": task,
        "task_id": task_id,
        "changed": True,
        "reason": reason.strip(),
        "message": f"Released {task_id}.",
    }
