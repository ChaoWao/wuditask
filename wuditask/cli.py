from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .configuration import load_config
from .dependencies import dependency_report
from .errors import WudiTaskError
from .github_delivery import (
    actor_eligibility,
    fetch_delivery,
    update_issue_assignee,
)
from .gitops import GitCoordinator
from .identity import detect_current_repo, resolve_identity
from .install import install_agent_access
from .model import claim_identity, claim_matches_identity
from .repository import TaskRepository
from .selfupdate import self_update
from .site_builder import build_site
from .util import (
    TASK_ID_RE,
    new_task_id,
    normalize_repo,
    read_json,
    repo_from_remote,
    timestamp_from_task_id,
    utc_now,
)
from .validation import validate_repository
from .workflow import archive_task, claim_task, create_task, release_task

HELP_COMMANDS = {
    "add": {
        "purpose": "Add a fully specified task for a GitHub work repository.",
        "usage": "wuditask add --source ISSUE_OR_PR_URL --title TEXT --goal TEXT --accept TEXT [--verify type::value] [--depends TASK_ID]",
        "agent_usage": {
            "codex": "$wuditask-add",
            "claude": "/wuditask-add",
        },
    },
    "execute": {
        "purpose": "Claim one unowned task whose dependencies are complete.",
        "usage": "wuditask execute [TASK_ID] [--repo owner/name]",
        "agent_usage": {
            "codex": "$wuditask-execute",
            "claude": "/wuditask-execute",
        },
    },
    "dep-check": {
        "purpose": "Expand dependencies and explain whether work is ready.",
        "usage": "wuditask dep-check [TASK_ID]",
        "agent_usage": {
            "codex": "$wuditask-dep-check",
            "claude": "/wuditask-dep-check",
        },
    },
    "archive": {
        "purpose": "Archive claimed work with an outcome and acceptance evidence.",
        "usage": "wuditask archive TASK_ID --outcome done --result TEXT --evidence AC-N=TEXT",
        "agent_usage": {
            "codex": "$wuditask-archive",
            "claude": "/wuditask-archive",
        },
    },
    "release": {
        "purpose": "Return a task owned by the current GitHub user to the queue.",
        "usage": "wuditask release TASK_ID --reason TEXT",
        "agent_usage": {
            "codex": "$wuditask-release",
            "claude": "/wuditask-release",
        },
    },
    "list": {
        "purpose": "List open, archived, or all tasks.",
        "usage": "wuditask list [--scope open|archive|all] [--repo owner/name]",
        "agent_usage": {
            "codex": "$wuditask-list",
            "claude": "/wuditask-list",
        },
    },
    "show": {
        "purpose": "Show one task and its derived dependency state.",
        "usage": "wuditask show TASK_ID",
        "agent_usage": {
            "codex": "$wuditask-show",
            "claude": "/wuditask-show",
        },
    },
    "reconcile": {
        "purpose": "Compare WudiTask coordination with live GitHub delivery state.",
        "usage": "wuditask reconcile [TASK_ID]",
        "agent_usage": {
            "codex": "$wuditask-reconcile",
            "claude": "/wuditask-reconcile",
        },
    },
    "install": {
        "purpose": "Register the tool clone and a separate task Hub remote.",
        "usage": "wuditask install --hub-remote URL [--hub-branch BRANCH] [--home PATH] [--replace]",
        "agent_usage": {
            "codex": "$wuditask-install",
            "claude": "/wuditask-install",
        },
    },
    "selfupdate": {
        "purpose": "Safely update the installed tool clone or maintain WudiTask.",
        "usage": "wuditask selfupdate [--check]",
        "agent_usage": {
            "codex": "$wuditask-selfupdate",
            "claude": "/wuditask-selfupdate",
            "codex_fix": "$wuditask-selfupdate fix <request>",
            "claude_fix": "/wuditask-selfupdate fix <request>",
        },
    },
}

GITHUB_SOURCE_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/"
    r"(?P<kind>issues|pull)/(?P<number>[1-9][0-9]*)/?$"
)


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
        help="Path to a local task Hub checkout; requires --local.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Read and write the explicit --hub checkout only.",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")
    parser.add_argument(
        "--actor",
        help=argparse.SUPPRESS,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add a fully specified task.")
    add.add_argument("--spec", type=str, help="JSON spec path, or - for stdin.")
    add.add_argument(
        "--id", dest="task_id", help="Explicit task ID for idempotent automation."
    )
    add.add_argument("--title")
    add.add_argument("--repo", help="Target GitHub repository in owner/name form.")
    add.add_argument("--goal")
    add.add_argument("--context", action="append")
    add.add_argument(
        "--accept", action="append", help="Acceptance criterion; repeat as needed."
    )
    add.add_argument(
        "--verify",
        action="append",
        help="Matching verification as type::value; repeat in --accept order.",
    )
    add.add_argument(
        "--depends", action="append", help="Dependency task ID; repeat as needed."
    )
    add.add_argument("--priority", choices=("P0", "P1", "P2", "P3"))
    add.add_argument(
        "--source",
        help="Canonical GitHub Issue or PR URL.",
    )
    add.add_argument(
        "--source-fallback-reason",
        help="Why the canonical GitHub source is outside the execution repository.",
    )
    add.add_argument(
        "--text-source-reason",
        help="Why no GitHub Issue or PR can carry the canonical narrative.",
    )
    add.add_argument("--link", action="append")

    execute = commands.add_parser("execute", help="Claim one ready, unowned task.")
    execute.add_argument("task_id", nargs="?")
    execute.add_argument(
        "--repo", help="Target repository; defaults to the current Git remote."
    )

    archive = commands.add_parser(
        "archive", help="Record a terminal task outcome in the archive."
    )
    archive.add_argument("task_id")
    archive.add_argument(
        "--outcome", choices=("done", "failed", "cancelled"), default="done"
    )
    archive.add_argument("--result")
    archive.add_argument(
        "--evidence",
        action="append",
        help="Acceptance evidence in AC-N=text form; repeat for each criterion.",
    )

    release = commands.add_parser("release", help="Return an owned task to the queue.")
    release.add_argument("task_id")
    release.add_argument("--reason")

    dep_check = commands.add_parser(
        "dep-check",
        help="Expand dependencies and report readiness.",
    )
    dep_check.add_argument("task_id", nargs="?")

    list_command = commands.add_parser("list", help="List open or archived tasks.")
    list_command.add_argument(
        "--scope",
        choices=("open", "archive", "all"),
        default="open",
    )
    list_command.add_argument("--repo")

    show = commands.add_parser(
        "show", help="Show one task with derived dependency state."
    )
    show.add_argument("task_id")

    reconcile = commands.add_parser(
        "reconcile",
        help="Compare queue coordination with canonical GitHub delivery.",
    )
    reconcile.add_argument("task_id", nargs="?")

    commands.add_parser(
        "validate", help="Validate all task files and dependency references."
    )

    build = commands.add_parser(
        "build-site", help="Build the static GitHub Pages artifact."
    )
    build.add_argument("--output", type=Path, default=Path("_site"))

    install = commands.add_parser(
        "install", help="Register this tool clone and a task Hub remote."
    )
    install.add_argument("--home", type=Path)
    install.add_argument("--replace", action="store_true")
    install.add_argument("--hub-remote", required=True)
    install.add_argument("--hub-branch", default="main")

    selfupdate = commands.add_parser(
        "selfupdate", help="Safely fast-forward this WudiTask clone."
    )
    selfupdate.add_argument(
        "--check",
        action="store_true",
        help="Fetch and report update state without changing the clone.",
    )

    help_command = commands.add_parser(
        "help", help="Show workflow and command examples."
    )
    help_command.add_argument(
        "topic",
        nargs="?",
        choices=("workflow", *HELP_COMMANDS),
    )
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
                f"Invalid JSON from stdin at line {exc.lineno}, column {exc.colno}.",
            ) from exc
    else:
        value = read_json(Path(path))
    if not isinstance(value, dict):
        raise WudiTaskError("invalid_task_spec", "Task spec must be a JSON object.")
    return value


def _verification(value: str) -> dict[str, str]:
    verification_type, separator, detail = value.partition("::")
    if not separator or not verification_type.strip() or not detail.strip():
        raise WudiTaskError(
            "invalid_verification",
            "Verification must use type::value form.",
            details={"value": value, "types": ["command", "file", "manual", "url"]},
        )
    return {"type": verification_type.strip(), "value": detail.strip()}


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
            "github_issue" if match.group("kind") == "issues" else "github_pull_request"
        ),
        "repo": normalize_repo(match.group("repo")),
        "number": int(match.group("number")),
    }


def _add_spec(args: argparse.Namespace) -> dict[str, Any]:
    spec = _read_spec(args.spec)
    direct = {
        "title": args.title,
        "repo": args.repo,
        "goal": args.goal,
        "context": args.context,
        "dependencies": args.depends,
        "priority": args.priority,
        "links": args.link,
    }
    for key, value in direct.items():
        if value is not None:
            spec[key] = value
    if args.source and args.text_source_reason:
        raise WudiTaskError(
            "multiple_task_sources",
            "Choose either a GitHub source or an explained text source.",
        )
    if args.source:
        spec["source"] = _github_source(args.source)
    if args.text_source_reason:
        spec["source"] = {
            "kind": "text",
            "reason": args.text_source_reason.strip(),
        }
    if args.source_fallback_reason:
        source = spec.get("source")
        if not isinstance(source, dict) or source.get("kind") != "github_issue":
            raise WudiTaskError(
                "fallback_reason_without_github_source",
                "--source-fallback-reason requires a GitHub Issue source.",
            )
        source["kind"] = "github_issue_fallback"
        source["fallback_reason"] = args.source_fallback_reason.strip()
    if not spec.get("repo"):
        detected = detect_current_repo()
        if detected:
            spec["repo"] = detected
    if args.accept is not None:
        verifications = args.verify or []
        if len(verifications) > len(args.accept):
            raise WudiTaskError(
                "verification_count_mismatch",
                "There cannot be more --verify values than --accept values.",
            )
        criteria = []
        for index, description in enumerate(args.accept):
            verification = (
                _verification(verifications[index])
                if index < len(verifications)
                else {"type": "manual", "value": description}
            )
            criteria.append(
                {
                    "description": description,
                    "verification": verification,
                }
            )
        spec["acceptance_criteria"] = criteria
    elif args.verify:
        raise WudiTaskError(
            "verification_without_criterion",
            "--verify requires matching --accept values.",
        )
    return spec


def _evidence(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        criterion_id, separator, text = value.partition("=")
        if not separator or not criterion_id.strip() or not text.strip():
            raise WudiTaskError(
                "invalid_evidence",
                "Evidence must use AC-N=text form.",
                details={"value": value},
            )
        criterion_id = criterion_id.strip()
        if criterion_id in result:
            raise WudiTaskError(
                "duplicate_evidence",
                f"Evidence for {criterion_id} was provided more than once.",
            )
        result[criterion_id] = text.strip()
    return result


def _validate_add_source(task: dict[str, Any], hub_repo: str | None) -> None:
    source = task["source"]
    if source["kind"] == "github_issue_fallback":
        if hub_repo is None:
            raise WudiTaskError(
                "hub_repository_unknown",
                "A fallback Issue requires a GitHub-backed configured Hub repository.",
                details={"source": source},
            )
        if source["repo"].casefold() != hub_repo.casefold():
            raise WudiTaskError(
                "invalid_fallback_repository",
                "A fallback Issue must belong to the configured WudiTask Hub.",
                details={
                    "source_repo": source["repo"],
                    "configured_hub_repo": hub_repo,
                },
            )
    if source["kind"] == "text":
        return
    delivery = fetch_delivery(source)
    if delivery["status"] != "fresh":
        raise WudiTaskError(
            "github_source_unavailable",
            "The canonical GitHub Issue or pull request could not be verified.",
            details={"source": source, "delivery": delivery},
            exit_code=3,
        )


def _issue_source(source: dict[str, Any]) -> bool:
    return source.get("kind") in {"github_issue", "github_issue_fallback"}


def _login_in(values: object, login: str) -> bool:
    return isinstance(values, list) and any(
        isinstance(value, str) and value.casefold() == login.casefold()
        for value in values
    )


def _actor_owns_active_pr(delivery: dict[str, Any], login: str) -> bool:
    for pr in delivery.get("prs") or []:
        if not isinstance(pr, dict) or pr.get("state") != "OPEN":
            continue
        author = pr.get("author")
        if isinstance(author, str) and author.casefold() == login.casefold():
            return True
        if _login_in(pr.get("assignees"), login):
            return True
    return False


def _claim_compensation(
    coordinator: GitCoordinator,
    actor: Any,
    claimed: dict[str, Any],
    *,
    reason: str,
    remove_assignment: bool,
) -> dict[str, Any]:
    source = claimed["task"]["source"]
    removal: dict[str, Any] = {
        "status": "not_needed",
        "changed": False,
        "error": None,
    }
    if remove_assignment:
        removal = update_issue_assignee(source, actor.login, add=False)
        if removal["status"] != "updated":
            return {
                "confirmed": False,
                "assignment_removal": removal,
                "lease": "retained",
            }
    token = claimed["task"]["claim"]["token"]
    try:
        released = coordinator.write(
            lambda repository: release_task(
                repository,
                actor,
                claimed["task_id"],
                reason=reason,
                expected_claim_token=token,
            ),
            actor,
            lambda result: (
                f"wuditask: release {result['task_id']} - GitHub claim reconciliation"
            ),
        )
    except WudiTaskError as error:
        return {
            "confirmed": False,
            "assignment_removal": removal,
            "lease": "retained_or_unknown",
            "release_error": error.as_dict()["error"],
        }
    return {
        "confirmed": True,
        "assignment_removal": removal,
        "release": released,
    }


def _claim_reconciliation_error(
    claimed: dict[str, Any],
    *,
    message: str,
    delivery: dict[str, Any],
    eligibility: dict[str, Any],
    compensation: dict[str, Any] | None = None,
) -> WudiTaskError:
    details: dict[str, Any] = {
        "task_id": claimed["task_id"],
        "delivery": delivery,
        "eligibility": eligibility,
    }
    if compensation is not None:
        details["compensation"] = compensation
    if compensation is None or not compensation.get("confirmed"):
        return WudiTaskError(
            "github_claim_reconciliation_required",
            f"{message} The execution lease was retained because cleanup was not requested or could not be confirmed.",
            details=details,
            exit_code=3,
        )
    return WudiTaskError(
        "github_claim_reconciliation_failed",
        f"{message} The execution lease was released.",
        details=details,
        exit_code=3,
    )


def _finalize_claim_delivery(
    coordinator: GitCoordinator,
    actor: Any,
    claimed: dict[str, Any],
) -> dict[str, Any]:
    source = claimed["task"]["source"]
    initial = claimed.get("delivery_eligibility") or {}
    if source.get("kind") == "text":
        claimed["work_authorized"] = True
        return claimed

    assignment: dict[str, Any] | None = None
    if initial.get("decision") == "available" and _issue_source(source):
        if not coordinator.distributed:
            compensation = _claim_compensation(
                coordinator,
                actor,
                claimed,
                reason="Local mode cannot assign a real GitHub Issue.",
                remove_assignment=False,
            )
            raise _claim_reconciliation_error(
                claimed,
                message="Local mode cannot establish GitHub ownership for an unassigned Issue.",
                delivery=claimed["delivery"],
                eligibility=initial,
                compensation=compensation,
            )
        assignment = update_issue_assignee(source, actor.login, add=True)

    delivery = fetch_delivery(source)
    eligibility = actor_eligibility(delivery, actor)

    if assignment is not None and assignment["status"] != "updated":
        if eligibility["eligible"] and eligibility["decision"] == "adopt":
            assignment = {
                **assignment,
                "status": "reconciled",
                "changed": True,
            }
        else:
            actor_assigned = _login_in(delivery.get("assignees"), actor.login)
            remove_assignment = actor_assigned or delivery["status"] != "fresh"
            compensation = _claim_compensation(
                coordinator,
                actor,
                claimed,
                reason="GitHub Issue assignment failed after acquiring the lease.",
                remove_assignment=remove_assignment,
            )
            raise _claim_reconciliation_error(
                claimed,
                message="GitHub Issue assignment could not be confirmed.",
                delivery=delivery,
                eligibility=eligibility,
                compensation=compensation,
            )

    decision = eligibility["decision"]
    acceptable_terminal = decision in {"verification_required", "cancelled"}
    if not ((eligibility["eligible"] and decision == "adopt") or acceptable_terminal):
        if not claimed.get("lease_acquired") and assignment is None:
            raise _claim_reconciliation_error(
                claimed,
                message="GitHub ownership no longer matches the existing execution lease.",
                delivery=delivery,
                eligibility=eligibility,
            )
        remove_assignment = assignment is not None and assignment.get("changed", False)
        compensation = _claim_compensation(
            coordinator,
            actor,
            claimed,
            reason="GitHub ownership changed while the lease was being established.",
            remove_assignment=remove_assignment,
        )
        raise _claim_reconciliation_error(
            claimed,
            message="GitHub ownership changed while the execution lease was being established.",
            delivery=delivery,
            eligibility=eligibility,
            compensation=compensation,
        )

    claimed["delivery"] = delivery
    claimed["delivery_eligibility"] = eligibility
    claimed["work_authorized"] = decision == "adopt"
    if assignment is not None:
        claimed["github_assignment"] = assignment
    return claimed


def _release_with_delivery(
    coordinator: GitCoordinator,
    actor: Any,
    task_id: str,
    reason: str | None,
) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise WudiTaskError(
            "release_reason_required",
            "Releasing a task requires a reason.",
            details={"question": "Why is this task being returned to the queue?"},
        )
    with coordinator.snapshot() as repository:
        record = repository.load_index().open.get(task_id)
        if record is None:
            raise WudiTaskError(
                "task_not_open",
                f"Task {task_id} is not open.",
                details={"task_id": task_id},
            )
        claim = record.task.get("claim")
        if claim is not None and not claim_matches_identity(claim, actor):
            raise WudiTaskError(
                "claim_holder_mismatch",
                f"Task {task_id} is claimed by another GitHub user.",
                details={"task_id": task_id, "claim_holder": claim_identity(claim)},
                exit_code=3,
            )
        claim_token = claim.get("token") if isinstance(claim, dict) else None
        expected_unclaimed = claim is None
        source = dict(record.task["source"])

    def write_release() -> dict[str, Any]:
        return coordinator.write(
            lambda repository: release_task(
                repository,
                actor,
                task_id,
                reason=reason,
                expected_claim_token=claim_token,
                expected_unclaimed=expected_unclaimed,
            ),
            actor,
            lambda result: (
                f"wuditask: release {result['task_id']} - "
                f"{result.get('reason', '').replace(chr(10), ' ')[:72]}"
            ),
        )

    if (
        expected_unclaimed
        or not coordinator.distributed
        or source.get("kind") == "text"
    ):
        return write_release()

    delivery = fetch_delivery(source)
    if delivery["status"] != "fresh":
        raise WudiTaskError(
            "github_delivery_unavailable",
            f"Task {task_id} cannot be released while GitHub delivery is unavailable.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )
    if _actor_owns_active_pr(delivery, actor.login):
        raise WudiTaskError(
            "active_pull_request_prevents_release",
            "The current user still owns an active closing pull request; close or transfer it before returning the task to the queue.",
            details={"task_id": task_id, "delivery": delivery},
            exit_code=3,
        )

    removal: dict[str, Any] | None = None
    if _issue_source(source) and _login_in(delivery.get("assignees"), actor.login):
        removal = update_issue_assignee(source, actor.login, add=False)
        if removal["status"] != "updated":
            raise WudiTaskError(
                "github_unassignment_failed",
                "The execution lease was retained because GitHub responsibility could not be released.",
                details={"task_id": task_id, "unassignment": removal},
                exit_code=3,
            )
        refreshed = fetch_delivery(source)
        if (
            refreshed["status"] != "fresh"
            or _login_in(refreshed.get("assignees"), actor.login)
            or _actor_owns_active_pr(refreshed, actor.login)
        ):
            restoration = update_issue_assignee(source, actor.login, add=True)
            raise WudiTaskError(
                "github_release_reconciliation_required",
                "GitHub responsibility could not be confirmed released; the WudiTask lease was retained.",
                details={
                    "task_id": task_id,
                    "delivery": refreshed,
                    "assignment_restoration": restoration,
                },
                exit_code=3,
            )
        delivery = refreshed

    try:
        result = write_release()
    except WudiTaskError as error:
        if removal is None:
            raise
        raise WudiTaskError(
            "github_release_reconciliation_required",
            "GitHub responsibility was removed, but the WudiTask lease release could not be confirmed; retry release or reconcile before any work starts.",
            details={
                "task_id": task_id,
                "release_error": error.as_dict()["error"],
                "github_unassignment": removal,
            },
            exit_code=3,
        ) from error

    result["delivery"] = delivery
    if removal is not None:
        result["github_unassignment"] = removal
    return result


def _help(topic: str | None) -> dict[str, Any]:
    workflow = [
        "add: record a task with a repository, goal, and acceptance criteria",
        "execute: claim one ready and unowned task; start work only after confirmed push",
        "dep-check: inspect cross-repository blockers and completion evidence",
        "archive: preserve a done, failed, or cancelled result instead of deleting it",
    ]
    selected = (
        {topic: HELP_COMMANDS[topic]}
        if topic and topic != "workflow"
        else HELP_COMMANDS
    )
    return {
        "message": "WudiTask help",
        "topic": topic or "workflow",
        "cli_invocation": "wuditask help [topic]",
        "workflow": workflow,
        "commands": [{"name": name, **details} for name, details in selected.items()],
        "notes": [
            "Use the operation-specific agent skill shown for each command.",
            "Task commands use hub_remote and hub_branch from config; the tool origin is never used as the Hub.",
            "For add, use a matching Issue or PR in the execution repository; use a configured Hub Issue only when that repository cannot host the narrative.",
            "GitHub owns delivery progress; WudiTask owns the execution lease, dependencies, and verified archive outcome.",
            "Selfupdate fix directly maintains WudiTask in an isolated worktree; it does not create an Issue or queue task.",
            "Run commands from the target work repository so owner/name can be detected from origin.",
            "Remote writes use the human identity from gh api user.",
            "Never start work until execute returns confirmed=true, sync.confirmed=true, and work_authorized=true.",
            "Use --json before the command for stable agent-readable output.",
        ],
    }


def _list_tasks(
    repository: TaskRepository, scope: str, repo_filter: str | None
) -> dict[str, Any]:
    index = repository.load_index()
    normalized = normalize_repo(repo_filter) if repo_filter else None
    open_reports = dependency_report(index)["tasks"]
    for report in open_reports:
        record = index.open[report["id"]]
        report["delivery"] = fetch_delivery(record.task["source"])
    open_tasks = [
        report
        for report in open_reports
        if normalized is None or report["repo"] == normalized
    ]
    archived_tasks = [
        {
            **record.task,
            "delivery": fetch_delivery(record.task["source"]),
        }
        for record in index.archived.values()
        if normalized is None or record.task["repo"] == normalized
    ]
    archived_tasks.sort(
        key=lambda task: (task["completion"]["completed_at"], task["id"]),
        reverse=True,
    )
    result: dict[str, Any] = {"scope": scope}
    if scope in {"open", "all"}:
        result["open_tasks"] = open_tasks
    if scope in {"archive", "all"}:
        result["archived_tasks"] = archived_tasks
    result["count"] = sum(
        len(value)
        for key, value in result.items()
        if key in {"open_tasks", "archived_tasks"}
    )
    return result


def _show_task(repository: TaskRepository, task_id: str) -> dict[str, Any]:
    index = repository.load_index()
    record = index.get(task_id)
    if record is None:
        raise WudiTaskError(
            "task_not_found",
            f"Task {task_id} does not exist.",
            details={"task_id": task_id},
        )
    result: dict[str, Any] = {
        "location": "archive" if record.archived else "open",
        "task": record.task,
    }
    result["dependency_status"] = dependency_report(index, task_id)["task"]
    result["delivery"] = fetch_delivery(record.task["source"])
    return result


def _reconcile_task(record: Any, index: Any) -> dict[str, Any]:
    task = record.task
    coordination = dependency_report(index, task["id"])["task"]
    delivery = fetch_delivery(task["source"])
    observations: list[dict[str, str]] = []
    delivery_state = delivery["delivery_state"]
    claim_holder = coordination.get("claim_holder")
    delivery_owners = list(delivery.get("assignees") or [])
    for pr in delivery.get("prs") or []:
        if pr.get("state") == "OPEN" and pr.get("author"):
            delivery_owners.append(pr["author"])
        if pr.get("state") == "OPEN":
            delivery_owners.extend(pr.get("assignees") or [])
    owner_keys = {value.casefold() for value in delivery_owners}

    if delivery["status"] != "fresh":
        observations.append(
            {
                "code": "delivery_unavailable",
                "message": delivery.get("error") or "GitHub delivery is unavailable.",
            }
        )
    elif record.archived and task["source"].get("kind") != "text":
        outcome = task["completion"]["outcome"]
        expected = {
            "done": {"verification_needed"},
            "cancelled": {"cancelled"},
            "failed": {"cancelled", "verification_needed"},
        }[outcome]
        if delivery_state not in expected:
            observations.append(
                {
                    "code": "archived_outcome_delivery_mismatch",
                    "message": (
                        f"Archived outcome {outcome} expects GitHub delivery "
                        f"in {sorted(expected)}, but it is {delivery_state}."
                    ),
                }
            )
    elif delivery_state in {"assigned", "implementing", "review", "ready_to_merge"}:
        if claim_holder is None:
            observations.append(
                {
                    "code": "external_delivery_without_lease",
                    "message": "GitHub shows active work while WudiTask has no execution lease.",
                }
            )
        elif claim_holder["login"].casefold() not in owner_keys:
            observations.append(
                {
                    "code": "claim_delivery_mismatch",
                    "message": "The WudiTask claim holder is not a GitHub assignee or active closing-PR author.",
                }
            )
        elif owner_keys - {claim_holder["login"].casefold()}:
            observations.append(
                {
                    "code": "claim_delivery_multiple_owners",
                    "message": "GitHub has active owners in addition to the WudiTask claim holder.",
                }
            )
    elif delivery_state == "unstarted" and claim_holder is not None:
        observations.append(
            {
                "code": "github_assignment_missing",
                "message": "WudiTask is claimed but the canonical GitHub Issue is unassigned.",
            }
        )
    elif delivery_state == "verification_needed" and not record.archived:
        observations.append(
            {
                "code": "verification_needed",
                "message": "GitHub delivery completed; WudiTask acceptance evidence still needs archival.",
            }
        )
    elif delivery_state == "cancelled" and not record.archived:
        observations.append(
            {
                "code": "cancellation_needed",
                "message": "GitHub delivery was cancelled; the open WudiTask needs an explicit outcome.",
            }
        )

    return {
        "task_id": task["id"],
        "repo": task["repo"],
        "coordination": coordination,
        "delivery": delivery,
        "observations": observations,
        "consistent": not observations,
    }


def _reconcile(repository: TaskRepository, task_id: str | None) -> dict[str, Any]:
    index = repository.load_index()
    if task_id:
        record = index.get(task_id)
        if record is None:
            raise WudiTaskError(
                "task_not_found",
                f"Task {task_id} does not exist.",
                details={"task_id": task_id},
            )
        reports = [_reconcile_task(record, index)]
    else:
        reports = [
            _reconcile_task(record, index)
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
        "count": len(reports),
        "inconsistent": sum(not report["consistent"] for report in reports),
    }


def _text(result: dict[str, Any]) -> str:
    if isinstance(result.get("commands"), list):
        lines = ["WudiTask workflow"]
        for step in result.get("workflow", []):
            lines.append(f"  {step}")
        lines.extend(["", "Commands"])
        for command in result["commands"]:
            lines.append(f"  {command['name']}: {command['purpose']}")
            lines.append(f"    {command['usage']}")
            for mode, invocation in command.get("agent_usage", {}).items():
                lines.append(f"    {mode}: {invocation}")
        lines.extend(
            [
                "",
                "CLI help",
                f"  {result['cli_invocation']}",
            ]
        )
        if result.get("notes"):
            lines.extend(["", "Notes"])
            for note in result["notes"]:
                lines.append(f"  {note}")
        return "\n".join(lines)
    if isinstance(result.get("message"), str):
        return result["message"]
    if isinstance(result.get("tasks"), list) and "inconsistent" in result:
        if not result["tasks"]:
            return "No tasks to reconcile."
        lines = [
            "TASK ID                         QUEUE        DELIVERY             RESULT"
        ]
        for report in result["tasks"]:
            observations = report.get("observations") or []
            outcome = (
                ", ".join(item.get("code", "unknown") for item in observations)
                if observations
                else "consistent"
            )
            lines.append(
                f"{str(report.get('task_id', '')):<31} "
                f"{str(report.get('coordination', {}).get('state', 'unknown')):<12} "
                f"{str(report.get('delivery', {}).get('delivery_state', 'unavailable')):<20} "
                f"{outcome}"
            )
            for observation in observations:
                lines.append(f"  - {observation.get('message', '')}")
        return "\n".join(lines)
    tasks = result.get("tasks") or result.get("open_tasks")
    if isinstance(tasks, list):
        if not tasks:
            return "No tasks."
        lines = [
            "QUEUE        DELIVERY             PRI  TASK ID                         REPOSITORY          TITLE"
        ]
        for task in tasks:
            lines.append(
                f"{str(task.get('state', 'archived')):<12} "
                f"{str(task.get('delivery', {}).get('delivery_state', 'unavailable')):<20} "
                f"{str(task.get('priority', '-')):<4} "
                f"{str(task.get('id', '')):<31} "
                f"{str(task.get('repo', '')):<19} "
                f"{task.get('title', '')}"
            )
        return "\n".join(lines)
    if isinstance(result.get("task"), dict):
        task = result["task"]
        return json.dumps(task, indent=2, ensure_ascii=False)
    return json.dumps(result, indent=2, ensure_ascii=False)


def _emit(result: dict[str, Any], as_json: bool) -> None:
    payload = {"ok": True, **result}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(_text(result))


def _emit_error(error: WudiTaskError, as_json: bool) -> None:
    if as_json:
        print(json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(f"wuditask: {error.message}", file=sys.stderr)
        if error.details is not None:
            print(
                json.dumps(error.details, indent=2, ensure_ascii=False), file=sys.stderr
            )


def run(args: argparse.Namespace, tool_root: Path) -> dict[str, Any]:
    tool_root = tool_root.expanduser().resolve()
    if args.command == "help":
        return _help(args.topic)
    if args.command == "selfupdate":
        if args.local:
            raise WudiTaskError(
                "selfupdate_local_mode_invalid",
                "Self-update synchronizes with origin and cannot use --local.",
            )
        if args.hub is not None:
            raise WudiTaskError(
                "selfupdate_hub_path_invalid",
                "Self-update acts only on the tool clone and does not accept --hub.",
            )
        return self_update(tool_root, check_only=args.check)
    if args.command == "install":
        if args.local or args.hub is not None:
            raise WudiTaskError(
                "install_local_mode_invalid",
                "Install registers the tool clone and a remote Hub; it does not accept --local or --hub.",
            )
        return install_agent_access(
            tool_root,
            hub_remote=args.hub_remote,
            hub_branch=args.hub_branch,
            home=args.home,
            replace=args.replace,
        )
    hub_repo: str | None
    if args.local:
        if args.hub is None:
            raise WudiTaskError(
                "local_hub_required",
                "Local mode requires an explicit --hub path.",
            )
        coordinator = GitCoordinator(local_root=args.hub.expanduser().resolve())
        hub_repo = detect_current_repo(coordinator.root)
    else:
        if args.hub is not None:
            raise WudiTaskError(
                "remote_hub_path_invalid",
                "Remote mode uses hub_remote from config and does not accept --hub.",
            )
        config = load_config(expected_tool_path=tool_root)
        coordinator = GitCoordinator(
            remote=config.hub_remote,
            branch=config.hub_branch,
        )
        hub_repo = repo_from_remote(config.hub_remote)

    if args.command in {"add", "execute", "archive", "release"}:
        if args.actor and coordinator.distributed:
            raise WudiTaskError(
                "actor_override_local_only",
                "Actor override is allowed only with --local; remote writes must use gh identity.",
            )
        actor = resolve_identity(args.actor)
        if args.command == "add":
            spec = _add_spec(args)
            created_at = (
                timestamp_from_task_id(args.task_id) if args.task_id else utc_now()
            )
            if created_at is None:
                created_at = utc_now()
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
        if args.command == "execute":
            target_repo = args.repo or detect_current_repo()
            claimed = coordinator.write(
                lambda repository: claim_task(
                    repository,
                    actor,
                    repo=target_repo,
                    task_id=args.task_id,
                ),
                actor,
                lambda result: f"wuditask: claim {result['task_id']}",
            )
            return _finalize_claim_delivery(coordinator, actor, claimed)
        if args.command == "archive":
            evidence = _evidence(args.evidence)
            return coordinator.write(
                lambda repository: archive_task(
                    repository,
                    actor,
                    args.task_id,
                    outcome=args.outcome,
                    result=args.result,
                    evidence=evidence,
                ),
                actor,
                lambda result: f"wuditask: archive {result['task_id']} ({args.outcome})",
            )
        return _release_with_delivery(
            coordinator,
            actor,
            args.task_id,
            args.reason,
        )

    with coordinator.snapshot() as repository:
        if args.command == "dep-check":
            index = repository.load_index()
            result = dependency_report(index, args.task_id)
            reports = [result["task"]] if args.task_id else result["tasks"]
            for report in reports:
                record = index.get(report["id"])
                if record is not None:
                    report["delivery"] = fetch_delivery(record.task["source"])
            return result
        if args.command == "list":
            return _list_tasks(repository, args.scope, args.repo)
        if args.command == "show":
            return _show_task(repository, args.task_id)
        if args.command == "reconcile":
            return _reconcile(repository, args.task_id)
        if args.command == "validate":
            return validate_repository(repository)
        if args.command == "build-site":
            output = args.output
            if not output.is_absolute():
                output = Path.cwd() / output
            hub_repo = (
                repo_from_remote(coordinator.remote)
                if coordinator.remote
                else detect_current_repo(repository.root)
            )
            return build_site(
                repository.load_index(),
                source=tool_root / "site",
                output=output,
                hub_repo=hub_repo,
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
        error = WudiTaskError(
            "interrupted", "Operation was interrupted.", exit_code=130
        )
        _emit_error(error, args.json)
        return error.exit_code
    _emit(result, args.json)
    return 0
