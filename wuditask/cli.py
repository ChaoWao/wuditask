from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .configuration import load_config
from .dependencies import task_dependency_report
from .errors import WudiTaskError
from .github_delivery import fetch_delivery, update_source_assignee
from .gitops import GitCoordinator
from .identity import detect_current_repo, resolve_identity
from .install import install_agent_access
from .model import Identity
from .repository import TaskIndex, TaskRecord, TaskRepository
from .selfupdate import self_update
from .site_builder import build_site
from .util import (
    TASK_ID_RE,
    new_run_id,
    new_task_id,
    normalize_repo,
    read_json,
    repo_from_remote,
    timestamp_from_task_id,
    utc_now,
)
from .validation import validate_repository
from .workflow import (
    archive_task,
    create_task,
    delete_archived_tasks,
    release_agent,
    start_agent,
)


HELP_COMMANDS = {
    "add": ("Add a GitHub-backed task reference.", "$wuditask-add"),
    "assign": ("Assign GitHub responsibility without starting work.", "$wuditask-assign"),
    "check": ("Read current dependencies, owners, agents, and delivery.", "$wuditask-check"),
    "execute": ("Start one confirmed agent run on ready work.", "$wuditask-execute"),
    "release": ("Stop one exact agent run without unassigning.", "$wuditask-release"),
    "unassign": ("Remove GitHub responsibility after agents stop.", "$wuditask-unassign"),
    "archive": ("Archive a terminal outcome with evidence.", "$wuditask-archive"),
    "delete": ("Delete explicitly identified erroneous archive records.", "$wuditask-delete"),
    "list": ("List queue entries with live GitHub state.", "$wuditask-list"),
    "show": ("Show one task and both sources of truth.", "$wuditask-show"),
    "install": ("Install the tool and separate Hub access.", "$wuditask-install"),
    "selfupdate": ("Update or directly maintain WudiTask.", "$wuditask-selfupdate"),
}

GITHUB_SOURCE_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/"
    r"(?P<kind>issues|pull)/(?P<number>[1-9][0-9]*)/?$"
)
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
TERMINAL_DELIVERY_STATES = {"verification_needed", "cancelled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wuditask",
        description="Coordinate GitHub-backed tasks without a central server.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--hub",
        type=Path,
        help="Path to an explicit local Hub checkout; requires --local.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Read or perform supported development writes in --hub only.",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")
    parser.add_argument("--actor", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add a canonical GitHub task reference.")
    add.add_argument("--spec", help="JSON spec path, or - for stdin.")
    add.add_argument("--id", dest="task_id")
    add.add_argument("--repo", help="Execution repository in owner/name form.")
    add.add_argument("--source", help="Canonical GitHub Issue or PR URL.")
    add.add_argument("--source-fallback-reason")
    add.add_argument("--depends", action="append")
    add.add_argument("--priority", choices=("P0", "P1", "P2", "P3"))

    assign = commands.add_parser("assign", help="Add a canonical GitHub assignee.")
    assign.add_argument("task_id")
    assign.add_argument("--to", dest="target_login")

    unassign = commands.add_parser(
        "unassign", help="Remove a canonical GitHub assignee."
    )
    unassign.add_argument("task_id")
    unassign.add_argument("--from", dest="target_login")

    execute = commands.add_parser("execute", help="Start one ready agent run.")
    execute.add_argument("task_id", nargs="?")
    execute.add_argument(
        "--repo", help="Execution repository; defaults to the current Git remote."
    )

    release = commands.add_parser("release", help="Stop one exact agent run.")
    release.add_argument("task_id")
    release.add_argument("--run-id", required=True)
    release.add_argument("--reason")

    archive = commands.add_parser("archive", help="Archive a terminal task outcome.")
    archive.add_argument("task_id")
    archive.add_argument("--run-id")
    archive.add_argument(
        "--outcome", choices=("done", "failed", "cancelled"), default="done"
    )
    archive.add_argument("--result", required=True)
    archive.add_argument("--evidence", action="append")

    delete = commands.add_parser(
        "delete", help="Delete erroneous archived task records."
    )
    delete.add_argument("task_ids", nargs="+")
    delete.add_argument("--reason", required=True)

    check = commands.add_parser("check", help="Check latest Hub and GitHub state.")
    check.add_argument("task_id", nargs="?")
    check.add_argument("--repo")

    list_command = commands.add_parser("list", help="List open or archived tasks.")
    list_command.add_argument(
        "--scope", choices=("open", "archive", "all"), default="open"
    )
    list_command.add_argument("--repo")

    show = commands.add_parser("show", help="Show one task.")
    show.add_argument("task_id")

    commands.add_parser("validate", help="Validate Hub data and dependencies.")
    site = commands.add_parser("build-site", help="Build the GitHub Pages artifact.")
    site.add_argument("--output", type=Path, default=Path("_site"))

    install = commands.add_parser("install", help="Register tool and Hub remotes.")
    install.add_argument("--home", type=Path)
    install.add_argument("--replace", action="store_true")
    install.add_argument("--hub-remote", required=True)
    install.add_argument("--hub-branch", default="main")

    update = commands.add_parser("selfupdate", help="Fast-forward the installed tool.")
    update.add_argument("--check", action="store_true")

    help_command = commands.add_parser("help", help="Show workflow help.")
    help_command.add_argument("topic", nargs="?", choices=("workflow", *HELP_COMMANDS))
    return parser


def _read_spec(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    if path == "-":
        try:
            value = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise WudiTaskError(
                "invalid_json",
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}.",
            ) from exc
    else:
        value = read_json(Path(path))
    if not isinstance(value, dict):
        raise WudiTaskError("invalid_task_spec", "Task spec must be a JSON object.")
    return value


def _github_source(value: str) -> dict[str, Any]:
    match = GITHUB_SOURCE_URL_RE.fullmatch(value.strip())
    if match is None:
        raise WudiTaskError(
            "invalid_github_source",
            "Source must be a canonical GitHub Issue or pull request URL.",
            details={"value": value},
        )
    return {
        "kind": (
            "github_issue"
            if match.group("kind") == "issues"
            else "github_pull_request"
        ),
        "repo": normalize_repo(match.group("repo")),
        "number": int(match.group("number")),
    }


def _add_spec(args: argparse.Namespace) -> dict[str, Any]:
    spec = _read_spec(args.spec)
    if args.repo is not None:
        spec["repo"] = args.repo
    if args.source is not None:
        spec["source"] = _github_source(args.source)
    if args.depends is not None:
        spec["dependencies"] = args.depends
    if args.priority is not None:
        spec["priority"] = args.priority
    if args.source_fallback_reason is not None:
        source = spec.get("source")
        if not isinstance(source, dict) or source.get("kind") != "github_issue":
            raise WudiTaskError(
                "fallback_reason_without_issue",
                "--source-fallback-reason requires an Issue source.",
            )
        source["kind"] = "github_issue_fallback"
        source["fallback_reason"] = args.source_fallback_reason.strip()
    if not spec.get("repo"):
        detected = detect_current_repo()
        if detected:
            spec["repo"] = detected
    return spec


def _validate_add_source(task: dict[str, Any], hub_repo: str | None) -> None:
    source = task["source"]
    if source["kind"] == "github_issue_fallback":
        if hub_repo is None:
            raise WudiTaskError(
                "hub_repository_unknown",
                "A fallback Issue requires a GitHub-backed configured Hub.",
            )
        if source["repo"].casefold() != hub_repo.casefold():
            raise WudiTaskError(
                "invalid_fallback_repository",
                "A fallback Issue must belong to the configured WudiTask Hub.",
                details={"source_repo": source["repo"], "hub_repo": hub_repo},
            )
    delivery = fetch_delivery(source)
    if delivery.get("status") != "fresh":
        raise WudiTaskError(
            "github_source_unavailable",
            "The canonical GitHub Issue or pull request could not be verified.",
            details={"source": source, "delivery": delivery},
            exit_code=3,
        )


def _login_in(values: object, login: str) -> bool:
    return isinstance(values, list) and any(
        isinstance(value, str) and value.casefold() == login.casefold()
        for value in values
    )


def _target_login(value: str) -> str:
    login = value.strip()
    if not GITHUB_LOGIN_RE.fullmatch(login):
        raise WudiTaskError(
            "invalid_github_login",
            "GitHub login must contain only letters, numbers, and internal hyphens.",
            details={"value": value},
        )
    return login


def _active_for(task: dict[str, Any], login: str) -> dict[str, str] | None:
    return next(
        (
            agent
            for agent in task.get("active_agents", [])
            if isinstance(agent, dict)
            and isinstance(agent.get("login"), str)
            and agent["login"].casefold() == login.casefold()
        ),
        None,
    )


def _task_snapshot(coordinator: GitCoordinator, task_id: str) -> dict[str, Any]:
    with coordinator.snapshot() as repository:
        index = repository.load_index()
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
        return deepcopy(record.task)


def _require_fresh_delivery(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    delivery = fetch_delivery(task["source"])
    if delivery.get("status") != "fresh":
        raise WudiTaskError(
            "github_delivery_unavailable",
            f"Task {task_id} cannot be changed while GitHub is unavailable.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    return delivery


def _assign_task(
    coordinator: GitCoordinator,
    actor: Identity,
    task_id: str,
    target_login: str,
) -> dict[str, Any]:
    task = _task_snapshot(coordinator, task_id)
    delivery = _require_fresh_delivery(task_id, task)
    if delivery.get("delivery_state") in TERMINAL_DELIVERY_STATES:
        raise WudiTaskError(
            "delivery_not_assignable",
            f"Task {task_id} has terminal GitHub delivery.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    if _login_in(delivery.get("owners"), target_login):
        return {
            "task_id": task_id,
            "target_login": target_login,
            "assigned_by": actor.login,
            "confirmed": True,
            "changed": False,
            "already_owner": True,
            "delivery": delivery,
            "message": f"{target_login} is already an owner of {task_id}.",
        }
    mutation = update_source_assignee(task["source"], target_login, add=True)
    if mutation.get("status") != "updated":
        raise WudiTaskError(
            "github_assignment_failed",
            f"Could not assign {target_login} to {task_id}.",
            details={"task_id": task_id, "assignment": mutation},
            exit_code=3,
        )
    refreshed = _require_fresh_delivery(task_id, task)
    if not _login_in(refreshed.get("assignees"), target_login) or not _login_in(
        refreshed.get("owners"), target_login
    ):
        raise WudiTaskError(
            "github_assignment_unconfirmed",
            f"GitHub did not confirm {target_login} as an assignee and owner.",
            details={"task_id": task_id, "delivery": refreshed},
            exit_code=3,
        )
    return {
        "task_id": task_id,
        "target_login": target_login,
        "assigned_by": actor.login,
        "confirmed": True,
        "changed": True,
        "already_owner": False,
        "delivery": refreshed,
        "github_assignment": mutation,
        "message": f"Assigned {target_login} to {task_id}.",
    }


def _unassign_task(
    coordinator: GitCoordinator,
    actor: Identity,
    task_id: str,
    target_login: str,
) -> dict[str, Any]:
    task = _task_snapshot(coordinator, task_id)
    active = _active_for(task, target_login)
    if active is not None:
        raise WudiTaskError(
            "active_agent_prevents_unassign",
            f"{target_login} still has an active agent run on {task_id}.",
            details={"task_id": task_id, "active_agent": active},
            exit_code=3,
        )
    delivery = _require_fresh_delivery(task_id, task)
    if not _login_in(delivery.get("assignees"), target_login):
        return {
            "task_id": task_id,
            "target_login": target_login,
            "unassigned_by": actor.login,
            "confirmed": True,
            "changed": False,
            "already_unassigned": True,
            "still_owner": _login_in(delivery.get("owners"), target_login),
            "delivery": delivery,
            "message": f"{target_login} is not an assignee of {task_id}.",
        }
    mutation = update_source_assignee(task["source"], target_login, add=False)
    if mutation.get("status") != "updated":
        raise WudiTaskError(
            "github_unassignment_failed",
            f"Could not unassign {target_login} from {task_id}.",
            details={"task_id": task_id, "unassignment": mutation},
            exit_code=3,
        )
    refreshed = _require_fresh_delivery(task_id, task)
    if _login_in(refreshed.get("assignees"), target_login):
        raise WudiTaskError(
            "github_unassignment_unconfirmed",
            f"GitHub still reports {target_login} as an assignee.",
            details={"task_id": task_id, "delivery": refreshed},
            exit_code=3,
        )
    latest = _task_snapshot(coordinator, task_id)
    raced_active = _active_for(latest, target_login)
    if raced_active is not None:
        restoration = update_source_assignee(task["source"], target_login, add=True)
        raise WudiTaskError(
            "github_unassignment_raced_active_agent",
            "An agent started during unassignment; GitHub responsibility was restored when possible.",
            details={
                "task_id": task_id,
                "active_agent": raced_active,
                "assignment_restoration": restoration,
            },
            exit_code=3,
        )
    return {
        "task_id": task_id,
        "target_login": target_login,
        "unassigned_by": actor.login,
        "confirmed": True,
        "changed": True,
        "already_unassigned": False,
        "still_owner": _login_in(refreshed.get("owners"), target_login),
        "delivery": refreshed,
        "github_unassignment": mutation,
        "message": f"Unassigned {target_login} from {task_id}.",
    }


def _select_execute_task(
    coordinator: GitCoordinator,
    actor: Identity,
    *,
    repo: str,
    task_id: str | None,
) -> dict[str, Any]:
    with coordinator.snapshot() as repository:
        index = repository.load_index()
        if task_id is not None:
            if task_id in index.archived:
                raise WudiTaskError(
                    "task_already_archived",
                    f"Task {task_id} has already been archived.",
                    exit_code=3,
                )
            record = index.open.get(task_id)
            if record is None:
                raise WudiTaskError("task_not_found", f"Task {task_id} does not exist.")
            candidates = [record]
        else:
            candidates = sorted(
                index.open.values(),
                key=lambda item: (
                    item.task["priority"],
                    item.task["created_at"],
                    item.task["id"],
                ),
            )
        candidates = [
            record
            for record in candidates
            if record.task["repo"].casefold() == repo.casefold()
        ]
        if task_id is not None and not candidates:
            raise WudiTaskError(
                "repository_mismatch",
                f"Task {task_id} does not belong to {repo}.",
                details={"task_id": task_id, "repo": repo},
            )

        assigned: list[dict[str, Any]] = []
        unowned: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for record in candidates:
            task = record.task
            active = _active_for(task, actor.login)
            if active is not None:
                detail = {
                    "task_id": task["id"],
                    "reason": "current login already has an active agent",
                    "active_agent": active,
                }
                if task_id is not None:
                    raise WudiTaskError(
                        "active_agent_conflict",
                        f"{actor.login} already has an active run on {task_id}.",
                        details=detail,
                        exit_code=3,
                    )
                blocked.append(detail)
                continue
            dependencies = task_dependency_report(record, index)
            if not dependencies["ready"]:
                detail = {
                    "task_id": task["id"],
                    "reason": "dependencies are blocked",
                    "blockers": dependencies["blockers"],
                }
                if task_id is not None:
                    raise WudiTaskError(
                        "dependency_blocked",
                        f"Task {task_id} has blocked dependencies.",
                        details=detail,
                        exit_code=3,
                    )
                blocked.append(detail)
                continue
            delivery = fetch_delivery(task["source"])
            if delivery.get("status") != "fresh" or delivery.get(
                "delivery_state"
            ) in TERMINAL_DELIVERY_STATES:
                detail = {
                    "task_id": task["id"],
                    "reason": "delivery is unavailable or terminal",
                    "delivery": delivery,
                }
                if task_id is not None:
                    raise WudiTaskError(
                        "delivery_not_executable",
                        f"Task {task_id} does not have executable GitHub delivery.",
                        details=detail,
                        exit_code=3,
                    )
                blocked.append(detail)
                continue
            owners = delivery.get("owners") if isinstance(delivery.get("owners"), list) else []
            candidate = {
                "task": deepcopy(task),
                "delivery": delivery,
                "dependency_check": dependencies,
            }
            if _login_in(owners, actor.login):
                assigned.append(candidate)
            elif not owners:
                unowned.append(candidate)
            else:
                detail = {
                    "task_id": task["id"],
                    "reason": "owned only by other users",
                    "owners": owners,
                }
                if task_id is not None:
                    unowned.append(candidate)
                else:
                    blocked.append(detail)
        if assigned:
            return {**assigned[0], "needs_assignment": False}
        if unowned:
            return {**unowned[0], "needs_assignment": True}
        raise WudiTaskError(
            "no_ready_task",
            f"No ready task is available for {actor.login} in {repo}.",
            details={"repo": repo, "blocked": blocked},
            exit_code=3,
        )


def _finalize_agent_delivery(
    coordinator: GitCoordinator,
    actor: Identity,
    started: dict[str, Any],
) -> dict[str, Any]:
    delivery = fetch_delivery(started["task"]["source"])
    authorized = (
        delivery.get("status") == "fresh"
        and delivery.get("delivery_state") not in TERMINAL_DELIVERY_STATES
        and _login_in(delivery.get("owners"), actor.login)
    )
    if authorized:
        started["delivery"] = delivery
        started["repo"] = started["task"]["repo"]
        started["work_authorized"] = True
        return started

    try:
        compensation = coordinator.write(
            lambda repository: release_agent(
                repository,
                actor,
                started["task_id"],
                run_id=started["run_id"],
                reason="Post-push GitHub ownership or delivery check failed.",
            ),
            actor,
            lambda result: f"wuditask: release {result['task_id']} after delivery drift",
        )
    except WudiTaskError as error:
        raise WudiTaskError(
            "execution_reconciliation_required",
            "GitHub no longer authorizes work and the exact agent run could not be released.",
            details={
                "task_id": started["task_id"],
                "run_id": started["run_id"],
                "delivery": delivery,
                "release_error": error.as_dict()["error"],
            },
            exit_code=3,
        ) from error
    raise WudiTaskError(
        "execution_reconciliation_failed",
        "GitHub no longer authorizes work; the just-started agent run was released.",
        details={
            "task_id": started["task_id"],
            "run_id": started["run_id"],
            "delivery": delivery,
            "compensation": compensation,
        },
        exit_code=3,
    )


def _execute(
    coordinator: GitCoordinator,
    actor: Identity,
    *,
    task_id: str | None,
    repo: str | None,
) -> dict[str, Any]:
    if not coordinator.distributed:
        raise WudiTaskError(
            "execute_remote_hub_required",
            "Agent execution requires the configured remote Hub and an ordinary no-force push.",
        )
    target_repo = repo or detect_current_repo()
    if target_repo is None:
        raise WudiTaskError(
            "execution_repository_required",
            "Run execute from the work repository or provide --repo owner/name.",
        )
    target_repo = normalize_repo(target_repo)
    selected = _select_execute_task(
        coordinator,
        actor,
        repo=target_repo,
        task_id=task_id,
    )
    assignment = None
    if selected["needs_assignment"]:
        assignment = _assign_task(
            coordinator,
            actor,
            selected["task"]["id"],
            actor.login,
        )
    run_id = new_run_id()
    started = coordinator.write(
        lambda repository: start_agent(
            repository,
            actor,
            task_id=selected["task"]["id"],
            repo=target_repo,
            run_id=run_id,
        ),
        actor,
        lambda result: f"wuditask: execute {result['task_id']} ({actor.login})",
    )
    if assignment is not None:
        started["github_assignment"] = assignment
    return _finalize_agent_delivery(coordinator, actor, started)


def _check_task(record: TaskRecord, index: TaskIndex) -> dict[str, Any]:
    task = record.task
    coordination = task_dependency_report(record, index)
    delivery = fetch_delivery(task["source"])
    owners = (
        delivery.get("owners")
        if delivery.get("status") == "fresh"
        and isinstance(delivery.get("owners"), list)
        else None
    )
    observations: list[dict[str, str]] = []
    if delivery.get("status") != "fresh":
        observations.append(
            {
                "code": "github_delivery_unavailable",
                "message": delivery.get("error") or "GitHub delivery is unavailable.",
            }
        )
    for agent in task.get("active_agents", []):
        if owners is not None and not _login_in(owners, agent["login"]):
            observations.append(
                {
                    "code": "active_agent_not_owner",
                    "message": f"Active agent {agent['login']} is no longer a live owner.",
                }
            )
    state = delivery.get("delivery_state")
    if not record.archived and state in TERMINAL_DELIVERY_STATES:
        observations.append(
            {
                "code": "archive_required",
                "message": "GitHub delivery is terminal but the task remains open.",
            }
        )
    if record.archived and delivery.get("status") == "fresh":
        outcome = task["completion"]["outcome"]
        expected = {
            "done": {"verification_needed"},
            "failed": TERMINAL_DELIVERY_STATES,
            "cancelled": {"cancelled"},
        }[outcome]
        if state not in expected:
            observations.append(
                {
                    "code": "archived_outcome_delivery_mismatch",
                    "message": f"Archived {outcome} does not match delivery state {state}.",
                }
            )
    return {
        "id": task["id"],
        "location": "archive" if record.archived else "open",
        "task": task,
        "dependency_check": coordination,
        "delivery": delivery,
        "owners": owners,
        "active_agents": task.get("active_agents", []),
        "observations": observations,
        "consistent": not observations,
    }


def _check(
    repository: TaskRepository,
    task_id: str | None,
    repo_filter: str | None,
) -> dict[str, Any]:
    index = repository.load_index()
    normalized = normalize_repo(repo_filter) if repo_filter else None
    if task_id is not None:
        record = index.get(task_id)
        if record is None:
            raise WudiTaskError("task_not_found", f"Task {task_id} does not exist.")
        reports = [_check_task(record, index)]
    else:
        records = sorted(
            index.all.values(),
            key=lambda item: (
                item.archived,
                item.task["priority"],
                item.task["created_at"],
                item.task["id"],
            ),
        )
        reports = [
            _check_task(record, index)
            for record in records
            if normalized is None or record.task["repo"] == normalized
        ]
    summary = {
        "checked": len(reports),
        "ready": sum(
            report["location"] == "open"
            and report["dependency_check"]["state"] == "ready"
            for report in reports
        ),
        "blocked": sum(
            report["location"] == "open"
            and report["dependency_check"]["state"] == "blocked"
            for report in reports
        ),
        "in_progress": sum(
            report["location"] == "open"
            and report["dependency_check"]["state"] == "in_progress"
            for report in reports
        ),
        "archived": sum(report["location"] == "archive" for report in reports),
        "drift": sum(not report["consistent"] for report in reports),
    }
    return {
        "tasks": reports,
        "count": len(reports),
        "inconsistent": summary["drift"],
        "summary": summary,
        "checked_at": utc_now(),
    }


def _list_tasks(
    repository: TaskRepository, scope: str, repo_filter: str | None
) -> dict[str, Any]:
    index = repository.load_index()
    normalized = normalize_repo(repo_filter) if repo_filter else None
    open_tasks = []
    for record in sorted(
        index.open.values(),
        key=lambda item: (
            item.task["priority"],
            item.task["created_at"],
            item.task["id"],
        ),
    ):
        if normalized is None or record.task["repo"] == normalized:
            open_tasks.append(
                {
                    **record.task,
                    "derived": task_dependency_report(record, index),
                    "delivery": fetch_delivery(record.task["source"]),
                }
            )
    archived_tasks = []
    for record in sorted(
        index.archived.values(),
        key=lambda item: (
            item.task["completion"]["completed_at"],
            item.task["id"],
        ),
        reverse=True,
    ):
        if normalized is None or record.task["repo"] == normalized:
            archived_tasks.append(
                {
                    **record.task,
                    "derived": task_dependency_report(record, index),
                    "delivery": fetch_delivery(record.task["source"]),
                }
            )
    result: dict[str, Any] = {"scope": scope}
    if scope in {"open", "all"}:
        result["open_tasks"] = open_tasks
    if scope in {"archive", "all"}:
        result["archived_tasks"] = archived_tasks
    result["count"] = sum(
        len(result.get(key, [])) for key in ("open_tasks", "archived_tasks")
    )
    return result


def _show_task(repository: TaskRepository, task_id: str) -> dict[str, Any]:
    index = repository.load_index()
    record = index.get(task_id)
    if record is None:
        raise WudiTaskError("task_not_found", f"Task {task_id} does not exist.")
    return _check_task(record, index)


def _help(topic: str | None) -> dict[str, Any]:
    names = [topic] if topic and topic != "workflow" else list(HELP_COMMANDS)
    return {
        "message": "WudiTask help",
        "topic": topic or "workflow",
        "cli_invocation": "wuditask help [topic]",
        "workflow": [
            "GitHub Issue or PR is the task contract and live owner source.",
            "assign changes GitHub responsibility; execute separately starts a Hub agent run.",
            "execute prefers ready work assigned to you, then self-assigns ready unowned work.",
            "check refreshes dependencies, owners, active agents, PRs, reviews, and checks.",
            "release stops only the matching run; archive records a verified terminal outcome.",
        ],
        "commands": [
            {
                "name": name,
                "purpose": HELP_COMMANDS[name][0],
                "agent_usage": {"codex": HELP_COMMANDS[name][1]},
            }
            for name in names
        ],
        "notes": [
            "The tool repository and task Hub are separate remotes.",
            "Hub writes use ordinary no-force pushes.",
            "Start work only after execute returns work_authorized=true and sync.confirmed=true.",
            "There are no dep-check or reconcile compatibility commands; use check.",
        ],
    }


def _text(result: dict[str, Any]) -> str:
    if isinstance(result.get("commands"), list):
        lines = ["WudiTask workflow"]
        lines.extend(f"  {step}" for step in result.get("workflow", []))
        lines.append("")
        lines.append("Commands")
        for command in result["commands"]:
            lines.append(f"  {command['name']}: {command['purpose']}")
            for product, invocation in command.get("agent_usage", {}).items():
                lines.append(f"    {product}: {invocation}")
        return "\n".join(lines)
    if isinstance(result.get("message"), str):
        return result["message"]
    reports = result.get("tasks")
    if isinstance(reports, list):
        if not reports:
            return "No tasks."
        lines = ["TASK ID                         HUB          GITHUB               OWNERS / ACTIVE"]
        for report in reports:
            if "dependency_check" in report:
                owners = ",".join(report.get("owners") or []) or "unknown"
                active = ",".join(
                    agent["login"] for agent in report.get("active_agents", [])
                ) or "-"
                lines.append(
                    f"{report['id']:<31} "
                    f"{report['dependency_check']['state']:<12} "
                    f"{report['delivery'].get('delivery_state', 'unavailable'):<20} "
                    f"{owners} / {active}"
                )
        return "\n".join(lines)
    if isinstance(result.get("task"), dict):
        return json.dumps(result, indent=2, ensure_ascii=False)
    return json.dumps(result, indent=2, ensure_ascii=False)


def _emit(result: dict[str, Any], as_json: bool) -> None:
    payload = {"ok": True, **result}
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if as_json
        else _text(result)
    )


def _emit_error(error: WudiTaskError, as_json: bool) -> None:
    if as_json:
        print(json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True))
        return
    print(f"wuditask: {error.message}", file=sys.stderr)
    if error.details is not None:
        print(json.dumps(error.details, indent=2, ensure_ascii=False), file=sys.stderr)


def _coordinator(
    args: argparse.Namespace,
    tool_root: Path,
) -> tuple[GitCoordinator, str | None]:
    if args.local:
        if args.hub is None:
            raise WudiTaskError(
                "local_hub_required", "Local mode requires an explicit --hub path."
            )
        coordinator = GitCoordinator(local_root=args.hub.expanduser().resolve())
        return coordinator, detect_current_repo(coordinator.root)
    if args.hub is not None:
        raise WudiTaskError(
            "remote_hub_path_invalid",
            "Remote mode uses the installed hub_remote and does not accept --hub.",
        )
    config = load_config(expected_tool_path=tool_root)
    return (
        GitCoordinator(remote=config.hub_remote, branch=config.hub_branch),
        repo_from_remote(config.hub_remote),
    )


def _write_actor(args: argparse.Namespace, coordinator: GitCoordinator) -> Identity:
    if coordinator.distributed and (args.actor or os.environ.get("WUDITASK_ACTOR")):
        raise WudiTaskError(
            "actor_override_local_only",
            "Remote mutations must use the authenticated gh login.",
        )
    return resolve_identity(args.actor)


def run(args: argparse.Namespace, tool_root: Path) -> dict[str, Any]:
    tool_root = tool_root.expanduser().resolve()
    if args.command == "help":
        return _help(args.topic)
    if args.command == "selfupdate":
        if args.local or args.hub is not None:
            raise WudiTaskError(
                "selfupdate_hub_invalid",
                "Self-update acts only on the registered tool clone.",
            )
        return self_update(tool_root, check_only=args.check)
    if args.command == "install":
        if args.local or args.hub is not None:
            raise WudiTaskError(
                "install_hub_invalid",
                "Install registers a remote Hub and does not accept local Hub flags.",
            )
        return install_agent_access(
            tool_root,
            hub_remote=args.hub_remote,
            hub_branch=args.hub_branch,
            home=args.home,
            replace=args.replace,
        )

    coordinator, hub_repo = _coordinator(args, tool_root)
    if args.command in {"execute", "release", "archive", "delete"} and not coordinator.distributed:
        code = f"{args.command}_remote_hub_required"
        raise WudiTaskError(
            code,
            f"{args.command} requires the configured remote Hub as its atomic Git boundary.",
        )

    if args.command in {
        "add",
        "assign",
        "unassign",
        "execute",
        "release",
        "archive",
        "delete",
    }:
        actor = _write_actor(args, coordinator)
        if args.command == "add":
            spec = _add_spec(args)
            created_at = timestamp_from_task_id(args.task_id) if args.task_id else utc_now()
            created_at = created_at or utc_now()
            task_id = args.task_id or new_task_id(created_at)
            if not TASK_ID_RE.fullmatch(task_id):
                raise WudiTaskError(
                    "invalid_task_id",
                    "Task ID must match WDT-YYYYMMDDTHHMMSSZ-XXXXXX.",
                    details={"value": task_id},
                )
            return coordinator.write(
                lambda repository: create_task(
                    repository,
                    spec,
                    actor,
                    task_id=task_id,
                    now=created_at,
                    source_guard=lambda task: _validate_add_source(task, hub_repo),
                ),
                actor,
                lambda result: f"wuditask: add {result['task_id']}",
            )
        if args.command == "assign":
            return _assign_task(
                coordinator,
                actor,
                args.task_id,
                _target_login(args.target_login or actor.login),
            )
        if args.command == "unassign":
            return _unassign_task(
                coordinator,
                actor,
                args.task_id,
                _target_login(args.target_login or actor.login),
            )
        if args.command == "execute":
            return _execute(
                coordinator,
                actor,
                task_id=args.task_id,
                repo=args.repo,
            )
        if args.command == "release":
            return coordinator.write(
                lambda repository: release_agent(
                    repository,
                    actor,
                    args.task_id,
                    run_id=args.run_id,
                    reason=args.reason,
                ),
                actor,
                lambda result: f"wuditask: release {result['task_id']} ({actor.login})",
            )
        if args.command == "archive":
            return coordinator.write(
                lambda repository: archive_task(
                    repository,
                    actor,
                    args.task_id,
                    outcome=args.outcome,
                    result=args.result,
                    evidence=args.evidence or [],
                    run_id=args.run_id,
                ),
                actor,
                lambda result: f"wuditask: archive {result['task_id']} ({args.outcome})",
            )
        return coordinator.write(
            lambda repository: delete_archived_tasks(
                repository,
                actor,
                args.task_ids,
                reason=args.reason,
            ),
            actor,
            lambda result: f"wuditask: delete {len(result['deleted_task_ids'])} archived task(s)",
        )

    with coordinator.snapshot() as repository:
        if args.command == "check":
            return _check(repository, args.task_id, args.repo)
        if args.command == "list":
            return _list_tasks(repository, args.scope, args.repo)
        if args.command == "show":
            return _show_task(repository, args.task_id)
        if args.command == "validate":
            return validate_repository(repository)
        if args.command == "build-site":
            output = args.output if args.output.is_absolute() else Path.cwd() / args.output
            return build_site(
                repository.load_index(),
                source=tool_root / "site",
                output=output,
                hub_repo=(
                    repo_from_remote(coordinator.remote)
                    if coordinator.remote
                    else detect_current_repo(repository.root)
                ),
            )
    raise WudiTaskError("unknown_command", f"Unknown command: {args.command}")


def main(argv: Sequence[str] | None = None, *, default_tool: Path | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    tool_root = default_tool or Path(__file__).resolve().parents[1]
    try:
        result = run(args, tool_root)
    except WudiTaskError as error:
        _emit_error(error, args.json)
        return error.exit_code
    except KeyboardInterrupt:
        error = WudiTaskError("interrupted", "Operation was interrupted.", exit_code=130)
        _emit_error(error, args.json)
        return error.exit_code
    _emit(result, args.json)
    return 0
