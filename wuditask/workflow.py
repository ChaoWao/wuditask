from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .dependencies import task_dependency_report
from .errors import WudiTaskError
from .github_delivery import fetch_delivery
from .model import Identity, OUTCOMES, PRIORITIES, identity_matches, require_valid_task
from .repository import TaskIndex, TaskRecord, TaskRepository
from .util import (
    RUN_ID_RE,
    TASK_ID_RE,
    deletion_receipt_id,
    new_task_id,
    normalize_repo,
    utc_now,
)


def _spec_missing(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    questions: list[str] = []
    if not isinstance(spec.get("repo"), str) or not spec["repo"].strip():
        missing.append("repo")
        questions.append("Which GitHub repository (owner/name) contains the work?")
    if not isinstance(spec.get("source"), dict) or not spec["source"]:
        missing.append("source")
        questions.append("Which canonical GitHub Issue or pull request describes the work?")
    return missing, questions


def create_task(
    repository: TaskRepository,
    spec: dict[str, Any],
    actor: Identity,
    *,
    task_id: str | None = None,
    now: str | None = None,
    source_guard: Callable[[dict[str, Any]], None] | None = None,
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
    timestamp = now or utc_now()
    source = deepcopy(spec["source"])
    if isinstance(source.get("repo"), str):
        source["repo"] = normalize_repo(source["repo"])
    task = {
        "schema_version": 3,
        "id": task_id or new_task_id(timestamp),
        "repo": normalize_repo(spec["repo"]),
        "source": source,
        "created_by": actor.login,
        "priority": priority,
        "created_at": timestamp,
        "dependencies": list(dict.fromkeys(spec.get("dependencies") or [])),
        "active_agents": [],
    }
    require_valid_task(task, archived=False)
    index = repository.load_index()
    existing = index.get(task["id"])
    if existing is not None:
        if not existing.archived and existing.task == task:
            return {
                "task": existing.task,
                "task_id": task["id"],
                "confirmed": True,
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
    deletion_receipt = repository.deletion_receipt_for_task(task["id"])
    if deletion_receipt is not None:
        raise WudiTaskError(
            "task_id_deleted",
            f"Task ID {task['id']} is reserved by a deletion receipt.",
            details={
                "task_id": task["id"],
                "deletion_receipt": deletion_receipt["id"],
            },
            exit_code=3,
        )
    missing_dependencies = [
        dependency for dependency in task["dependencies"] if index.get(dependency) is None
    ]
    if missing_dependencies:
        raise WudiTaskError(
            "missing_dependency",
            "Every dependency must already exist in WudiTask.",
            details={"task_id": task["id"], "missing": missing_dependencies},
        )
    if source_guard is not None:
        source_guard(task)
    repository.add(task)
    return {
        "task": task,
        "task_id": task["id"],
        "confirmed": True,
        "already_added": False,
        "changed": True,
        "message": f"Added {task['id']}.",
    }


def _validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WudiTaskError(
            "invalid_run_id",
            "Agent run ID must use WDX- followed by 24 hexadecimal characters.",
            details={"value": run_id},
        )
    return run_id


def _record_for_start(
    index: TaskIndex,
    *,
    task_id: str | None,
    normalized_repo: str | None,
) -> list[TaskRecord]:
    if task_id is not None:
        if task_id in index.archived:
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
        if normalized_repo and record.task["repo"].casefold() != normalized_repo.casefold():
            raise WudiTaskError(
                "repository_mismatch",
                f"Task {task_id} belongs to {record.task['repo']}, not {normalized_repo}.",
                details={"task_id": task_id, "task_repo": record.task["repo"]},
            )
        return [record]
    if normalized_repo is None:
        raise WudiTaskError(
            "repository_required",
            "A repository is required when no task ID is provided.",
        )
    candidates = [
        record
        for record in index.open.values()
        if record.task["repo"].casefold() == normalized_repo.casefold()
    ]
    return sorted(
        candidates,
        key=lambda record: (
            record.task["priority"],
            record.task["created_at"],
            record.task["id"],
        ),
    )


def _delivery_owner(delivery: dict[str, Any], actor: Identity) -> bool:
    owners = delivery.get("owners")
    return isinstance(owners, list) and any(
        isinstance(owner, str) and owner.casefold() == actor.login.casefold()
        for owner in owners
    )


def _start_delivery_error(
    task_id: str,
    delivery: dict[str, Any],
    actor: Identity,
) -> WudiTaskError | None:
    if delivery.get("status") != "fresh" or delivery.get("delivery_state") == "unavailable":
        return WudiTaskError(
            "github_delivery_unavailable",
            f"Task {task_id} cannot start while GitHub delivery is unavailable.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    if not _delivery_owner(delivery, actor):
        return WudiTaskError(
            "delivery_owner_required",
            f"{actor.login} is not a live owner of task {task_id}.",
            details={
                "task_id": task_id,
                "actor": actor.login,
                "owners": delivery.get("owners", []),
                "delivery": delivery,
            },
            exit_code=3,
        )
    if delivery.get("delivery_state") in {"verification_needed", "cancelled"}:
        return WudiTaskError(
            "delivery_not_executable",
            f"Task {task_id} has terminal GitHub delivery.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    return None


def start_agent(
    repository: TaskRepository,
    actor: Identity,
    *,
    run_id: str,
    repo: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    run_id = _validate_run_id(run_id)
    index = repository.load_index()
    normalized_repo = normalize_repo(repo) if repo else None
    candidates = _record_for_start(
        index,
        task_id=task_id,
        normalized_repo=normalized_repo,
    )
    blocked: list[dict[str, Any]] = []
    for record in candidates:
        task_id_value = record.task["id"]
        dep_report = task_dependency_report(record, index)
        if not dep_report["ready"]:
            error = WudiTaskError(
                "dependency_blocked",
                f"Task {task_id_value} cannot start while dependencies are blocked.",
                details={"task_id": task_id_value, "blockers": dep_report["blockers"]},
                exit_code=3,
            )
            if task_id is not None:
                raise error
            blocked.append(error.details)
            continue
        delivery = fetch_delivery(record.task["source"])
        delivery_error = _start_delivery_error(task_id_value, delivery, actor)
        if delivery_error is not None:
            if task_id is not None:
                raise delivery_error
            blocked.append(delivery_error.details)
            continue

        existing = next(
            (
                agent
                for agent in record.task["active_agents"]
                if agent["login"].casefold() == actor.login.casefold()
            ),
            None,
        )
        if existing is not None:
            if existing["run_id"] != run_id:
                raise WudiTaskError(
                    "active_agent_conflict",
                    f"{actor.login} already has another active run on {task_id_value}.",
                    details={
                        "task_id": task_id_value,
                        "login": existing["login"],
                        "active_run_id": existing["run_id"],
                        "requested_run_id": run_id,
                    },
                    exit_code=3,
                )
            return {
                "task": record.task,
                "task_id": task_id_value,
                "run_id": run_id,
                "confirmed": True,
                "already_active": True,
                "agent_started": False,
                "changed": False,
                "dependency_check": dep_report,
                "delivery": delivery,
                "message": f"{actor.login} run {run_id} is already active on {task_id_value}.",
            }
        task = deepcopy(record.task)
        task["active_agents"].append({"login": actor.login, "run_id": run_id})
        repository.write_open(task)
        return {
            "task": task,
            "task_id": task_id_value,
            "run_id": run_id,
            "confirmed": True,
            "already_active": False,
            "agent_started": True,
            "changed": True,
            "dependency_check": dep_report,
            "delivery": delivery,
            "message": f"Started {actor.login} run {run_id} on {task_id_value}.",
        }
    raise WudiTaskError(
        "no_ready_task",
        "No ready task owned by the current actor is available.",
        details={"repo": normalized_repo, "blocked": blocked},
        exit_code=3,
    )


def _matching_active_agent(
    task: dict[str, Any],
    actor: Identity,
    run_id: str,
) -> dict[str, str]:
    existing = next(
        (
            agent
            for agent in task["active_agents"]
            if agent["login"].casefold() == actor.login.casefold()
        ),
        None,
    )
    if existing is None:
        raise WudiTaskError(
            "agent_not_active",
            f"{actor.login} is not active on task {task['id']}.",
            details={"task_id": task["id"], "login": actor.login},
            exit_code=3,
        )
    if existing["run_id"] != run_id:
        raise WudiTaskError(
            "active_agent_run_mismatch",
            f"{actor.login} has a different active run on task {task['id']}.",
            details={
                "task_id": task["id"],
                "login": existing["login"],
                "active_run_id": existing["run_id"],
                "requested_run_id": run_id,
            },
            exit_code=3,
        )
    return existing


def release_agent(
    repository: TaskRepository,
    actor: Identity,
    task_id: str,
    *,
    run_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    run_id = _validate_run_id(run_id)
    record = repository.load_index().open.get(task_id)
    if record is None:
        raise WudiTaskError(
            "task_not_open",
            f"Task {task_id} is not open.",
            details={"task_id": task_id},
        )
    matching = _matching_active_agent(record.task, actor, run_id)
    task = deepcopy(record.task)
    task["active_agents"] = [
        agent for agent in task["active_agents"] if agent != matching
    ]
    repository.write_open(task)
    return {
        "task": task,
        "task_id": task_id,
        "run_id": run_id,
        "confirmed": True,
        "agent_released": True,
        "changed": True,
        "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None,
        "message": f"Released {actor.login} run {run_id} from {task_id}.",
    }


def _normalize_evidence(evidence: object, *, outcome: str) -> list[str]:
    if not isinstance(evidence, list) or any(
        not isinstance(item, str) or not item.strip() for item in evidence
    ):
        raise WudiTaskError(
            "invalid_archive_evidence",
            "Archive evidence must be an array of non-empty strings.",
        )
    normalized = [item.strip() for item in evidence]
    if outcome == "done" and not normalized:
        raise WudiTaskError(
            "insufficient_archive_evidence",
            "Archiving done requires at least one verification evidence item.",
        )
    return normalized


def archive_task(
    repository: TaskRepository,
    actor: Identity,
    task_id: str,
    *,
    outcome: str,
    result: str | None,
    evidence: list[str],
    run_id: str | None,
    now: str | None = None,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise WudiTaskError(
            "invalid_outcome",
            "Outcome must be done, failed, or cancelled.",
            details={"value": outcome},
        )
    validated_run_id = _validate_run_id(run_id) if run_id is not None else None
    if not isinstance(result, str) or not result.strip():
        raise WudiTaskError(
            "archive_result_required",
            "Archiving requires a concise result or reason.",
        )
    normalized_evidence = _normalize_evidence(evidence, outcome=outcome)
    index = repository.load_index()
    existing_archive = index.archived.get(task_id)
    if existing_archive is not None:
        completion = existing_archive.task["completion"]
        participants = completion.get("participants", [])
        participant_matches = validated_run_id is not None and any(
            participant["login"].casefold() == actor.login.casefold()
            and participant["run_id"] == validated_run_id
            for participant in participants
            if isinstance(participant, dict)
        )
        creator_terminal_matches = (
            validated_run_id is None
            and participants == []
            and identity_matches(existing_archive.task.get("created_by"), actor)
        )
        if (
            (participant_matches or creator_terminal_matches)
            and identity_matches(completion.get("completed_by"), actor)
            and completion.get("outcome") == outcome
            and completion.get("result") == result.strip()
            and completion.get("evidence") == normalized_evidence
        ):
            return {
                "task": existing_archive.task,
                "task_id": task_id,
                "run_id": validated_run_id if participant_matches else None,
                "confirmed": True,
                "already_archived": True,
                "changed": False,
                "message": f"{task_id} is already archived by this actor.",
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
    active_agents = record.task["active_agents"]
    archive_run_id: str | None
    if active_agents:
        if validated_run_id is None:
            raise WudiTaskError(
                "archive_run_id_required",
                "Archiving a task with active agents requires the caller's run ID.",
                details={
                    "task_id": task_id,
                    "outcome": outcome,
                    "active_agents": [agent["login"] for agent in active_agents],
                },
            )
        _matching_active_agent(record.task, actor, validated_run_id)
        archive_run_id = validated_run_id
    else:
        if validated_run_id is not None:
            raise WudiTaskError(
                "archive_run_id_unexpected",
                "A terminal task without active agents must be archived without a run ID.",
                details={"task_id": task_id, "run_id": validated_run_id},
                exit_code=3,
            )
        if not identity_matches(record.task.get("created_by"), actor):
            raise WudiTaskError(
                "archive_creator_required",
                "A terminal task without active agents must be archived by its creator.",
                details={
                    "task_id": task_id,
                    "actor": actor.login,
                    "created_by": record.task.get("created_by"),
                },
                exit_code=3,
            )
        archive_run_id = None
    dep_report = task_dependency_report(record, index)
    if outcome == "done" and not dep_report["ready"]:
        raise WudiTaskError(
            "dependency_blocked",
            f"Task {task_id} cannot complete while dependencies are blocked.",
            details={"task_id": task_id, "blockers": dep_report["blockers"]},
            exit_code=3,
        )
    delivery = fetch_delivery(record.task["source"])
    if delivery.get("status") != "fresh":
        raise WudiTaskError(
            "github_delivery_unavailable",
            f"Task {task_id} cannot be archived while GitHub delivery is unavailable.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    if outcome == "done" and active_agents and not _delivery_owner(delivery, actor):
        raise WudiTaskError(
            "delivery_owner_required",
            f"{actor.login} is not a live owner of task {task_id}.",
            details={"task_id": task_id, "owners": delivery.get("owners", [])},
            exit_code=3,
        )
    allowed_states = {
        "done": {"verification_needed"},
        "failed": {"verification_needed", "cancelled"},
        "cancelled": {"cancelled"},
    }[outcome]
    if delivery.get("delivery_state") not in allowed_states:
        code = "github_delivery_incomplete" if outcome == "done" else "github_delivery_not_terminal"
        raise WudiTaskError(
            code,
            f"Task {task_id} cannot be archived {outcome} before GitHub delivery is terminal.",
            details={
                "task_id": task_id,
                "delivery": delivery,
                "allowed_delivery_states": sorted(allowed_states),
            },
            exit_code=3,
        )
    task = deepcopy(record.task)
    participants = deepcopy(task["active_agents"])
    task["active_agents"] = []
    task["completion"] = {
        "outcome": outcome,
        "completed_at": now or utc_now(),
        "completed_by": actor.login,
        "result": result.strip(),
        "evidence": normalized_evidence,
        "participants": participants,
    }
    repository.archive(task)
    return {
        "task": task,
        "task_id": task_id,
        "run_id": archive_run_id,
        "confirmed": True,
        "already_archived": False,
        "changed": True,
        "delivery": delivery,
        "message": f"Archived {task_id} with outcome {outcome}.",
    }


def delete_archived_tasks(
    repository: TaskRepository,
    actor: Identity,
    task_ids: list[str],
    *,
    reason: str | None,
    now: str | None = None,
) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise WudiTaskError(
            "delete_reason_required",
            "Deleting archived tasks requires a concrete reason.",
        )
    if not task_ids:
        raise WudiTaskError(
            "archived_task_ids_required",
            "Deleting archived tasks requires at least one exact task ID.",
        )
    invalid = [task_id for task_id in task_ids if not TASK_ID_RE.fullmatch(task_id)]
    if invalid:
        raise WudiTaskError(
            "invalid_task_id",
            "Every delete target must be a WudiTask ID.",
            details={"values": invalid},
        )
    duplicates = sorted(
        task_id for task_id in set(task_ids) if task_ids.count(task_id) > 1
    )
    if duplicates:
        raise WudiTaskError(
            "duplicate_task_id",
            "Each archived task ID may appear only once in a delete batch.",
            details={"values": duplicates},
        )
    reason = reason.strip()
    canonical_task_ids = sorted(task_ids)
    receipt_id = deletion_receipt_id(canonical_task_ids, reason, actor.login)
    index = repository.load_index()
    existing_receipt = repository.load_deletion_receipts().get(receipt_id)
    if existing_receipt is not None:
        recreated = [task_id for task_id in canonical_task_ids if index.get(task_id)]
        if recreated:
            raise WudiTaskError(
                "deleted_task_recreated",
                "A task covered by this deletion receipt exists again.",
                details={"receipt_id": receipt_id, "task_ids": recreated},
                exit_code=3,
            )
        return {
            "deleted_task_ids": canonical_task_ids,
            "deletion_receipt": existing_receipt,
            "deleted_by": existing_receipt["deleted_by"],
            "reason": existing_receipt["reason"],
            "confirmed": True,
            "already_deleted": True,
            "changed": False,
            "message": f"{len(canonical_task_ids)} archived task(s) were already deleted by this operation.",
        }
    invalid_targets = [
        {
            "task_id": task_id,
            "location": "open" if task_id in index.open else "missing",
        }
        for task_id in canonical_task_ids
        if task_id not in index.archived
    ]
    if invalid_targets:
        raise WudiTaskError(
            "archived_tasks_required",
            "Delete accepts only existing archived tasks.",
            details={"targets": invalid_targets},
            exit_code=3,
        )
    target_set = set(canonical_task_ids)
    dependent_map: dict[str, list[dict[str, str]]] = {
        task_id: [] for task_id in canonical_task_ids
    }
    for dependent_id, record in index.all.items():
        if dependent_id in target_set:
            continue
        for target_id in target_set.intersection(record.task.get("dependencies", [])):
            dependent_map[target_id].append(
                {
                    "task_id": dependent_id,
                    "location": "archive" if record.archived else "open",
                    "repo": record.task["repo"],
                }
            )
    blocked_targets = [
        {"task_id": task_id, "dependents": dependents}
        for task_id, dependents in dependent_map.items()
        if dependents
    ]
    if blocked_targets:
        raise WudiTaskError(
            "task_has_dependents",
            "Archived tasks cannot be deleted while other tasks depend on them.",
            details={"targets": blocked_targets},
            exit_code=3,
        )
    deleted_tasks = [
        {
            "id": task_id,
            "outcome": index.archived[task_id].task["completion"]["outcome"],
        }
        for task_id in canonical_task_ids
    ]
    receipt = {
        "receipt_version": 2,
        "id": receipt_id,
        "task_ids": canonical_task_ids,
        "reason": reason,
        "deleted_by": actor.login,
        "deleted_at": now or utc_now(),
    }
    repository.delete_archived(canonical_task_ids, receipt)
    return {
        "deleted_task_ids": canonical_task_ids,
        "deleted_tasks": deleted_tasks,
        "deletion_receipt": receipt,
        "deleted_by": actor.login,
        "reason": reason,
        "confirmed": True,
        "already_deleted": False,
        "changed": True,
        "message": f"Deleted {len(canonical_task_ids)} archived task(s).",
    }
