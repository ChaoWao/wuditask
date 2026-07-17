from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .dependencies import dependency_report, task_dependency_report
from .errors import WudiTaskError
from .github_delivery import actor_eligibility, fetch_delivery
from .model import (
    Identity,
    OUTCOMES,
    PRIORITIES,
    claim_identity,
    claim_matches_identity,
    identity_matches,
    require_valid_task,
)
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
    if not isinstance(spec.get("source"), dict) or not spec["source"]:
        missing.append("source")
        questions.append(
            "Which canonical GitHub Issue, PR, or explained text source describes the work?"
        )
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
    source = deepcopy(spec["source"])
    if source.get("kind") in {
        "github_issue",
        "github_pull_request",
        "github_issue_fallback",
    } and isinstance(source.get("repo"), str):
        source["repo"] = normalize_repo(source["repo"])
    task = {
        "schema_version": 2,
        "id": task_id or new_task_id(timestamp),
        "title": spec["title"].strip(),
        "repo": normalize_repo(spec["repo"]),
        "source": source,
        "created_by": actor.as_dict(),
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
    if source_guard is not None:
        source_guard(task)
    repository.add(task)
    return {
        "task": task,
        "task_id": task["id"],
        "confirmed": True,
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
            if claim_matches_identity(record.task.get("claim"), actor):
                delivery = fetch_delivery(record.task["source"])
                eligibility = actor_eligibility(delivery, actor)
                decision = eligibility["decision"]
                if not eligibility["eligible"] and decision not in {
                    "verification_required",
                    "cancelled",
                }:
                    code = {
                        "unavailable": "github_delivery_unavailable",
                        "owned_elsewhere": "delivery_owned_elsewhere",
                    }.get(decision, "delivery_not_executable")
                    raise WudiTaskError(
                        code,
                        f"Task {task_id} cannot resume because GitHub delivery is {decision}.",
                        details={
                            "task_id": task_id,
                            "delivery": delivery,
                            "eligibility": eligibility,
                        },
                        exit_code=3,
                    )
                task = record.task
                login_refreshed = task["claim"]["github_login"] != actor.login
                if login_refreshed:
                    task = deepcopy(task)
                    task["claim"]["github_login"] = actor.login
                    repository.write_open(task)
                return {
                    "task": task,
                    "task_id": task_id,
                    "confirmed": True,
                    "already_claimed": True,
                    "changed": login_refreshed,
                    "lease_acquired": False,
                    "claim_login_refreshed": login_refreshed,
                    "dependency_check": dependency_report(index, task_id)["task"],
                    "delivery": delivery,
                    "delivery_eligibility": eligibility,
                    "message": f"{task_id} is already claimed by {actor.login}.",
                }
            raise WudiTaskError(
                "claim_conflict",
                f"Task {task_id} is already claimed.",
                details={
                    "task_id": task_id,
                    "claim_holder": claim_identity(record.task.get("claim")),
                },
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
    selected_delivery = None
    selected_eligibility = None
    for candidate in candidates:
        report = task_dependency_report(candidate, index)
        if not report["ready"]:
            blocked.append(
                {"task_id": candidate.task["id"], "blockers": report["blockers"]}
            )
            continue
        delivery = fetch_delivery(candidate.task["source"])
        eligibility = actor_eligibility(delivery, actor)
        decision = eligibility["decision"]
        if eligibility["eligible"] or (
            task_id is not None and decision == "verification_required"
        ):
            selected = candidate
            selected_delivery = delivery
            selected_eligibility = eligibility
            break
        blocked.append(
            {
                "task_id": candidate.task["id"],
                "blockers": [
                    {
                        "id": candidate.task["id"],
                        "reason": f"GitHub delivery is {decision}",
                    }
                ],
                "delivery": delivery,
                "eligibility": eligibility,
            }
        )
    if selected is None:
        if task_id and blocked and "eligibility" in blocked[0]:
            decision = blocked[0]["eligibility"]["decision"]
            code = {
                "unavailable": "github_delivery_unavailable",
                "owned_elsewhere": "delivery_owned_elsewhere",
            }.get(decision, "delivery_not_executable")
            raise WudiTaskError(
                code,
                f"Task {task_id} cannot be claimed because GitHub delivery is {decision}.",
                details=blocked[0],
                exit_code=3,
            )
        raise WudiTaskError(
            "no_ready_task",
            "No unclaimed task with satisfied dependencies is available.",
            details={"repo": normalized_repo, "blocked": blocked},
            exit_code=3,
        )
    task = deepcopy(selected.task)
    claimed_at = now or utc_now()
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
        "lease_acquired": True,
        "dependency_check": dependency_report(repository.load_index(), task["id"])[
            "task"
        ],
        "delivery": selected_delivery,
        "delivery_eligibility": selected_eligibility,
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
    if outcome not in OUTCOMES:
        raise WudiTaskError(
            "invalid_outcome",
            "Outcome must be done, failed, or cancelled.",
            details={"value": outcome},
        )
    claim = task.get("claim")
    if outcome == "done" and claim is None:
        raise WudiTaskError(
            "task_not_claimed",
            f"Task {task_id} must be claimed before it can be archived done.",
            details={"task_id": task_id},
            exit_code=3,
        )
    if claim is not None and not claim_matches_identity(claim, actor):
        raise WudiTaskError(
            "claim_holder_mismatch",
            f"Task {task_id} is claimed by another GitHub user.",
            details={
                "task_id": task_id,
                "claim_holder": claim_identity(claim),
            },
            exit_code=3,
        )
    delivery = fetch_delivery(task["source"])
    if outcome == "done" and task["source"].get("kind") != "text":
        if delivery["status"] != "fresh":
            raise WudiTaskError(
                "github_delivery_unavailable",
                f"Task {task_id} cannot be archived done while GitHub delivery is unavailable.",
                details={"task_id": task_id, "delivery": delivery},
                exit_code=3,
            )
        if delivery["delivery_state"] != "verification_needed":
            raise WudiTaskError(
                "github_delivery_incomplete",
                f"Task {task_id} cannot be archived done before its canonical GitHub delivery completes.",
                details={"task_id": task_id, "delivery": delivery},
                exit_code=3,
            )
    elif outcome in {"failed", "cancelled"} and task["source"].get("kind") != "text":
        if delivery["status"] != "fresh":
            raise WudiTaskError(
                "github_delivery_unavailable",
                f"Task {task_id} cannot be archived {outcome} while GitHub delivery is unavailable.",
                details={"task_id": task_id, "delivery": delivery},
                exit_code=3,
            )
        allowed_states = (
            {"cancelled", "verification_needed"}
            if outcome == "failed"
            else {"cancelled"}
        )
        if delivery["delivery_state"] not in allowed_states:
            raise WudiTaskError(
                "github_delivery_not_terminal",
                f"Task {task_id} cannot be archived {outcome} while its canonical GitHub delivery is still active.",
                details={
                    "task_id": task_id,
                    "delivery": delivery,
                    "allowed_delivery_states": sorted(allowed_states),
                },
                exit_code=3,
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
        "delivery": delivery,
        "message": f"Archived {task_id} with outcome {outcome}.",
    }


def release_task(
    repository: TaskRepository,
    actor: Identity,
    task_id: str,
    *,
    reason: str | None,
    expected_claim_token: str | None = None,
    expected_unclaimed: bool = False,
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
    if expected_unclaimed and record.task.get("claim") is not None:
        raise WudiTaskError(
            "claim_state_changed",
            f"Task {task_id} acquired an execution lease after the release preflight.",
            details={
                "task_id": task_id,
                "claim_holder": claim_identity(record.task.get("claim")),
            },
            exit_code=3,
        )
    if record.task.get("claim") is None:
        return {
            "task": record.task,
            "task_id": task_id,
            "confirmed": True,
            "changed": False,
            "message": f"{task_id} is already unclaimed.",
        }
    if not claim_matches_identity(record.task.get("claim"), actor):
        raise WudiTaskError(
            "claim_holder_mismatch",
            f"Task {task_id} is claimed by another GitHub user.",
            details={
                "task_id": task_id,
                "claim_holder": claim_identity(record.task.get("claim")),
            },
            exit_code=3,
        )
    if (
        expected_claim_token is not None
        and record.task["claim"].get("token") != expected_claim_token
    ):
        raise WudiTaskError(
            "claim_token_mismatch",
            f"Task {task_id} now has a different execution lease.",
            details={"task_id": task_id},
            exit_code=3,
        )
    task = deepcopy(record.task)
    task["claim"] = None
    repository.write_open(task)
    return {
        "task": task,
        "task_id": task_id,
        "confirmed": True,
        "changed": True,
        "reason": reason.strip(),
        "message": f"Released {task_id}.",
    }
