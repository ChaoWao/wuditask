from __future__ import annotations

from dataclasses import dataclass
from .errors import DataValidationError
from .util import REPO_RE, RUN_ID_RE, TASK_ID_RE, is_utc_timestamp

SCHEMA_VERSION = 3
PRIORITIES = {"P0", "P1", "P2", "P3"}
OUTCOMES = {"done", "failed", "cancelled"}
GITHUB_SOURCE_KINDS = {
    "github_issue",
    "github_pull_request",
    "github_issue_fallback",
}


@dataclass(frozen=True)
class Identity:
    """Authenticated GitHub identity used by workflow operations.

    Login is the only durable identity in Hub data. GitHub's numeric database ID
    is deliberately not part of the task contract.
    """

    login: str

    def __post_init__(self) -> None:
        if not isinstance(self.login, str) or not self.login.strip():
            raise ValueError("identity login must be a non-empty string")
        object.__setattr__(self, "login", self.login.strip())

    def as_dict(self) -> str:
        # Kept as the serialization boundary used throughout the workflow.
        return self.login


def _issue(issues: list[dict[str, str]], path: str, message: str) -> None:
    issues.append({"path": path, "message": message})


def _check_string(
    value: object,
    path: str,
    issues: list[dict[str, str]],
    *,
    trimmed: bool = False,
) -> bool:
    if not isinstance(value, str):
        _issue(issues, path, "must be a string")
        return False
    if not value.strip():
        _issue(issues, path, "must not be empty")
        return False
    if trimmed and value != value.strip():
        _issue(issues, path, "must be trimmed")
        return False
    return True


def _validate_agents(
    value: object,
    path: str,
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(value, list):
        _issue(issues, path, "must be an array")
        return
    seen: set[str] = set()
    for index, agent in enumerate(value):
        base = f"{path}[{index}]"
        if not isinstance(agent, dict):
            _issue(issues, base, "must be an object")
            continue
        if set(agent) != {"login", "run_id"}:
            _issue(issues, base, "must contain only login and run_id")
        login = agent.get("login")
        if _check_string(login, f"{base}.login", issues, trimmed=True):
            folded = login.casefold()
            if folded in seen:
                _issue(
                    issues,
                    f"{base}.login",
                    "must be unique ignoring case",
                )
            seen.add(folded)
        run_id = agent.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            _issue(
                issues,
                f"{base}.run_id",
                "must match WDX- followed by 24 hexadecimal characters",
            )


def validate_task(
    task: object,
    *,
    archived: bool | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(task, dict):
        return [{"path": "$", "message": "must be a JSON object"}]

    required = {
        "schema_version",
        "id",
        "repo",
        "source",
        "created_by",
        "priority",
        "created_at",
        "dependencies",
        "active_agents",
    }
    allowed = required | {"completion"}
    for key in sorted(required - set(task)):
        _issue(issues, f"$.{key}", "is required")
    for key in sorted(set(task) - allowed):
        _issue(issues, f"$.{key}", "is not allowed")

    if task.get("schema_version") != SCHEMA_VERSION:
        _issue(issues, "$.schema_version", f"must equal {SCHEMA_VERSION}")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        _issue(issues, "$.id", "must match WDT-YYYYMMDDTHHMMSSZ-XXXXXX")
    repo = task.get("repo")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        _issue(issues, "$.repo", "must use owner/name form")
    _validate_source(task.get("source"), repo, issues)
    _check_string(task.get("created_by"), "$.created_by", issues, trimmed=True)
    if task.get("priority") not in PRIORITIES:
        _issue(issues, "$.priority", "must be one of P0, P1, P2, P3")
    if not is_utc_timestamp(task.get("created_at")):
        _issue(issues, "$.created_at", "must be a valid UTC timestamp ending in Z")

    dependencies = task.get("dependencies")
    seen_dependencies: set[str] = set()
    if not isinstance(dependencies, list):
        _issue(issues, "$.dependencies", "must be an array of task IDs")
    else:
        for index, dependency in enumerate(dependencies):
            path = f"$.dependencies[{index}]"
            if not isinstance(dependency, str) or not TASK_ID_RE.fullmatch(dependency):
                _issue(issues, path, "must be a WudiTask ID")
                continue
            if dependency == task_id:
                _issue(issues, path, "must not refer to the task itself")
            if dependency in seen_dependencies:
                _issue(issues, path, "must be unique")
            seen_dependencies.add(dependency)

    _validate_agents(task.get("active_agents"), "$.active_agents", issues)
    if archived is True and isinstance(task.get("active_agents"), list) and task["active_agents"]:
        _issue(issues, "$.active_agents", "must be empty in an archived task")

    completion = task.get("completion")
    if archived is False and "completion" in task:
        _issue(issues, "$.completion", "must not be present in an open task")
    if archived is True and completion is None:
        _issue(issues, "$.completion", "is required in an archived task")
    if completion is not None:
        _validate_completion(completion, task.get("created_by"), issues)
    return issues


def _validate_completion(
    completion: object,
    created_by: object,
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(completion, dict):
        _issue(issues, "$.completion", "must be an object")
        return
    expected = {
        "outcome",
        "completed_at",
        "completed_by",
        "result",
        "evidence",
        "participants",
    }
    if set(completion) != expected:
        _issue(issues, "$.completion", "contains unexpected or missing fields")
    outcome = completion.get("outcome")
    if outcome not in OUTCOMES:
        _issue(issues, "$.completion.outcome", "must be done, failed, or cancelled")
    if not is_utc_timestamp(completion.get("completed_at")):
        _issue(issues, "$.completion.completed_at", "must be a UTC timestamp")
    _check_string(
        completion.get("completed_by"),
        "$.completion.completed_by",
        issues,
        trimmed=True,
    )
    _check_string(completion.get("result"), "$.completion.result", issues, trimmed=True)

    evidence = completion.get("evidence")
    if not isinstance(evidence, list):
        _issue(issues, "$.completion.evidence", "must be an array")
    else:
        if outcome == "done" and not evidence:
            _issue(
                issues,
                "$.completion.evidence",
                "must be non-empty when outcome is done",
            )
        for index, item in enumerate(evidence):
            _check_string(
                item,
                f"$.completion.evidence[{index}]",
                issues,
                trimmed=True,
            )
    _validate_agents(completion.get("participants"), "$.completion.participants", issues)
    completed_by = completion.get("completed_by")
    participants = completion.get("participants")
    completed_by_is_participant = (
        isinstance(completed_by, str)
        and isinstance(participants, list)
        and any(
            isinstance(participant, dict)
            and isinstance(participant.get("login"), str)
            and participant["login"].casefold() == completed_by.casefold()
            for participant in participants
        )
    )
    unclaimed_terminal_by_creator = (
        outcome in {"failed", "cancelled"}
        and participants == []
        and isinstance(completed_by, str)
        and isinstance(created_by, str)
        and completed_by.casefold() == created_by.casefold()
    )
    if isinstance(completed_by, str) and not (
        completed_by_is_participant or unclaimed_terminal_by_creator
    ):
        message = (
            "must identify a participant or the task creator for a non-done outcome"
            if outcome in {"failed", "cancelled"} and participants == []
            else "must identify a participant"
        )
        _issue(
            issues,
            "$.completion.completed_by",
            message,
        )


def _validate_source(
    source: object,
    execution_repo: object,
    issues: list[dict[str, str]],
) -> None:
    path = "$.source"
    if not isinstance(source, dict):
        _issue(issues, path, "must be an object")
        return
    kind = source.get("kind")
    if kind not in GITHUB_SOURCE_KINDS:
        _issue(
            issues,
            f"{path}.kind",
            "must be github_issue, github_pull_request, or github_issue_fallback",
        )
        return
    fallback = kind == "github_issue_fallback"
    allowed = (
        {"kind", "repo", "number", "fallback_reason"}
        if fallback
        else {"kind", "repo", "number"}
    )
    if set(source) != allowed:
        _issue(
            issues,
            path,
            (
                "fallback Issue source must contain only kind, repo, number, and fallback_reason"
                if fallback
                else "GitHub source must contain only kind, repo, and number"
            ),
        )
    source_repo = source.get("repo")
    if not isinstance(source_repo, str) or not REPO_RE.fullmatch(source_repo):
        _issue(issues, f"{path}.repo", "must use owner/name form")
    number = source.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        _issue(issues, f"{path}.number", "must be a positive integer")
    same_repo = (
        isinstance(source_repo, str)
        and isinstance(execution_repo, str)
        and source_repo.casefold() == execution_repo.casefold()
    )
    if fallback:
        if same_repo:
            _issue(
                issues,
                f"{path}.repo",
                "fallback Issue must be outside the execution repository",
            )
        _check_string(
            source.get("fallback_reason"),
            f"{path}.fallback_reason",
            issues,
            trimmed=True,
        )
    elif not same_repo:
        _issue(
            issues,
            f"{path}.repo",
            "regular GitHub source must be in the execution repository; use github_issue_fallback for the configured Hub",
        )


def require_valid_task(task: object, *, archived: bool | None = None) -> None:
    issues = validate_task(task, archived=archived)
    if issues:
        raise DataValidationError(issues)


def identity_matches(value: object, identity: Identity) -> bool:
    return isinstance(value, str) and value.casefold() == identity.login.casefold()
