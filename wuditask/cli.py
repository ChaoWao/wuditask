from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .dependencies import dependency_report
from .errors import DataValidationError, WudiTaskError
from .gitops import GitCoordinator
from .identity import detect_current_repo, resolve_identity
from .install import install_agent_access
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
from .workflow import archive_task, claim_task, create_task, release_task

HELP_COMMANDS = {
    "add": {
        "purpose": "Add a fully specified task for a GitHub work repository.",
        "usage": "wuditask add --title TEXT --goal TEXT --accept TEXT [--verify type::value] [--link ISSUE_OR_PR_URL] [--depends TASK_ID]",
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
            "codex": "$wuditask-inspect",
            "claude": "/wuditask-inspect",
        },
    },
    "show": {
        "purpose": "Show one task and its derived dependency state.",
        "usage": "wuditask show TASK_ID",
        "agent_usage": {
            "codex": "$wuditask-inspect",
            "claude": "/wuditask-inspect",
        },
    },
    "install": {
        "purpose": "Register this clone through symlinks for Codex and Claude.",
        "usage": "wuditask install [--home PATH] [--replace]",
        "agent_usage": {
            "codex": "$wuditask-install",
            "claude": "/wuditask-install",
        },
    },
    "selfupdate": {
        "purpose": "Safely update the installed clone or directly maintain WudiTask.",
        "usage": "wuditask selfupdate [--check]",
        "agent_usage": {
            "update": "/wuditask-selfupdate",
            "fix": "/wuditask-selfupdate fix <request>",
            "codex_update": "$wuditask-selfupdate",
            "claude_update": "/wuditask-selfupdate",
            "codex_fix": "$wuditask-selfupdate fix <request>",
            "claude_fix": "/wuditask-selfupdate fix <request>",
        },
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wuditask",
        description="Coordinate GitHub-backed tasks without a central server.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--hub", type=Path, help="Path to the WudiTask clone.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Read and write this clone only; do not synchronize with origin.",
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
    add.add_argument("--link", action="append")

    execute = commands.add_parser("execute", help="Claim one ready, unowned task.")
    execute.add_argument("task_id", nargs="?")
    execute.add_argument(
        "--repo", help="Target repository; defaults to the current Git remote."
    )

    archive = commands.add_parser(
        "archive", help="Move a claimed task into the archive."
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

    commands.add_parser(
        "validate", help="Validate all task files and dependency references."
    )

    build = commands.add_parser(
        "build-site", help="Build the static GitHub Pages artifact."
    )
    build.add_argument("--output", type=Path, default=Path("_site"))

    install = commands.add_parser(
        "install", help="Register this clone for Codex and Claude."
    )
    install.add_argument("--home", type=Path)
    install.add_argument("--replace", action="store_true")

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


def _help(topic: str | None) -> dict[str, Any]:
    workflow = [
        "add: record a task with a repository, goal, and acceptance criteria",
        "execute: claim one ready and unowned task; start work only after confirmed push",
        "dep-check: inspect cross-repository blockers and completion evidence",
        "archive: preserve the result as done, failed, or cancelled instead of deleting it",
    ]
    selected = (
        {topic: HELP_COMMANDS[topic]}
        if topic and topic != "workflow"
        else HELP_COMMANDS
    )
    return {
        "message": "WudiTask help",
        "topic": topic or "workflow",
        "agent_invocation": {
            "codex": "$wuditask help [topic]",
            "claude": "/wuditask help [topic]",
        },
        "workflow": workflow,
        "commands": [{"name": name, **details} for name, details in selected.items()],
        "notes": [
            "Use the operation-specific agent skill shown for each command; $wuditask and /wuditask are the help router.",
            "For add, use a matching GitHub Issue or PR as the canonical narrative when the owning repository is clear.",
            "Selfupdate fix directly maintains WudiTask in an isolated worktree; it does not create an Issue or queue task.",
            "Run commands from the target work repository so owner/name can be detected from origin.",
            "Remote writes use the human identity from gh api user.",
            "Never start work until execute returns confirmed=true and sync.confirmed=true.",
            "Use --json before the command for stable agent-readable output.",
        ],
    }


def _validate_semantics(repository: TaskRepository) -> dict[str, Any]:
    issues = repository.validation_issues()
    if issues:
        raise DataValidationError(issues)
    index = repository.load_index()
    reports = dependency_report(index)["tasks"]
    semantic_issues: list[dict[str, str]] = []
    for report in reports:
        if report["cycle"]:
            semantic_issues.append(
                {
                    "path": report["id"],
                    "message": f"dependency cycle: {' -> '.join(report['cycle'])}",
                }
            )
        for dependency in report["dependencies"]:
            if not dependency["exists"]:
                semantic_issues.append(
                    {
                        "path": report["id"],
                        "message": f"missing dependency {dependency['id']}",
                    }
                )
    if semantic_issues:
        raise DataValidationError(semantic_issues)
    return {
        "message": "All task data and dependency references are valid.",
        "open": len(index.open),
        "archived": len(index.archived),
    }


def _list_tasks(
    repository: TaskRepository, scope: str, repo_filter: str | None
) -> dict[str, Any]:
    index = repository.load_index()
    normalized = normalize_repo(repo_filter) if repo_filter else None
    open_reports = dependency_report(index)["tasks"]
    open_tasks = [
        report
        for report in open_reports
        if normalized is None or report["repo"] == normalized
    ]
    archived_tasks = [
        record.task
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
    return result


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
                if command["name"] == "selfupdate" and mode in {"update", "fix"}:
                    continue
                lines.append(f"    {mode}: {invocation}")
        lines.extend(
            [
                "",
                "Agent invocation",
                f"  Codex: {result['agent_invocation']['codex']}",
                f"  Claude: {result['agent_invocation']['claude']}",
            ]
        )
        if result.get("notes"):
            lines.extend(["", "Notes"])
            for note in result["notes"]:
                lines.append(f"  {note}")
        return "\n".join(lines)
    if isinstance(result.get("message"), str):
        return result["message"]
    tasks = result.get("tasks") or result.get("open_tasks")
    if isinstance(tasks, list):
        if not tasks:
            return "No tasks."
        lines = [
            "STATE        PRI  TASK ID                         REPOSITORY          TITLE"
        ]
        for task in tasks:
            lines.append(
                f"{str(task.get('state', 'archived')):<12} "
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


def run(args: argparse.Namespace, hub_root: Path) -> dict[str, Any]:
    hub_root = (args.hub or hub_root).expanduser().resolve()
    if args.command == "help":
        return _help(args.topic)
    if args.command == "selfupdate":
        if args.local:
            raise WudiTaskError(
                "selfupdate_local_mode_invalid",
                "Self-update synchronizes with origin and cannot use --local.",
            )
        return self_update(hub_root, check_only=args.check)
    if args.command == "install":
        return install_agent_access(
            hub_root,
            home=args.home,
            replace=args.replace,
        )
    coordinator = GitCoordinator(hub_root, local_only=args.local)

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
                ),
                actor,
                lambda result: f"wuditask: add {result['task_id']}",
            )
        if args.command == "execute":
            target_repo = args.repo or detect_current_repo()
            return coordinator.write(
                lambda repository: claim_task(
                    repository,
                    actor,
                    repo=target_repo,
                    task_id=args.task_id,
                ),
                actor,
                lambda result: f"wuditask: claim {result['task_id']}",
            )
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
        return coordinator.write(
            lambda repository: release_task(
                repository,
                actor,
                args.task_id,
                reason=args.reason,
            ),
            actor,
            lambda result: (
                f"wuditask: release {result['task_id']} - "
                f"{result.get('reason', '').replace(chr(10), ' ')[:72]}"
            ),
        )

    with coordinator.snapshot() as repository:
        if args.command == "dep-check":
            return dependency_report(repository.load_index(), args.task_id)
        if args.command == "list":
            return _list_tasks(repository, args.scope, args.repo)
        if args.command == "show":
            return _show_task(repository, args.task_id)
        if args.command == "validate":
            return _validate_semantics(repository)
        if args.command == "build-site":
            output = args.output
            if not output.is_absolute():
                output = hub_root / output
            hub_repo = (
                repo_from_remote(coordinator.remote)
                if coordinator.remote
                else detect_current_repo(hub_root)
            )
            return build_site(
                repository.load_index(),
                source=repository.root / "site",
                output=output,
                hub_repo=hub_repo,
            )
    raise WudiTaskError("unknown_command", f"Unknown command: {args.command}")


def main(argv: Sequence[str] | None = None, *, default_hub: Path | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    hub_root = default_hub or Path(__file__).resolve().parents[1]
    try:
        result = run(args, hub_root)
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
