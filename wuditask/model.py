from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .errors import DataValidationError
from .util import REPO_RE, TASK_ID_RE, is_utc_timestamp

SCHEMA_VERSION = 2
PRIORITIES = {"P0", "P1", "P2", "P3"}
VERIFICATION_TYPES = {"command", "file", "manual", "url"}
OUTCOMES = {"done", "failed", "cancelled"}
RESULT_STATUSES = {"passed", "failed", "skipped"}
CRITERION_ID_RE = re.compile(r"^AC-[1-9][0-9]*$")
GITHUB_SOURCE_KINDS = {
    "github_issue",
    "github_pull_request",
    "github_issue_fallback",
}


@dataclass(frozen=True)
class Identity:
    login: str
    github_id: int

    def as_dict(self) -> dict[str, Any]:
        return {"login": self.login, "github_id": self.github_id}


def _issue(issues: list[dict[str, str]], path: str, message: str) -> None:
    issues.append({"path": path, "message": message})


def _check_string(
    value: object,
    path: str,
    issues: list[dict[str, str]],
    *,
    allow_empty: bool = False,
) -> bool:
    if not isinstance(value, str):
        _issue(issues, path, "must be a string")
        return False
    if not allow_empty and not value.strip():
        _issue(issues, path, "must not be empty")
        return False
    return True


def _check_identity(value: object, path: str, issues: list[dict[str, str]]) -> bool:
    if not isinstance(value, dict):
        _issue(issues, path, "must be an object")
        return False
    if set(value) != {"login", "github_id"}:
        _issue(issues, path, "must contain only login and github_id")
    _check_string(value.get("login"), f"{path}.login", issues)
    github_id = value.get("github_id")
    if not isinstance(github_id, int) or isinstance(github_id, bool) or github_id <= 0:
        _issue(issues, f"{path}.github_id", "must be a positive integer")
    return True


def validate_task(
    task: object, *, archived: bool | None = None
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(task, dict):
        return [{"path": "$", "message": "must be a JSON object"}]

    required = {
        "schema_version",
        "id",
        "title",
        "repo",
        "source",
        "created_by",
        "priority",
        "created_at",
        "goal",
        "context",
        "acceptance_criteria",
        "dependencies",
        "claim",
        "links",
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
    _check_string(task.get("title"), "$.title", issues)
    repo = task.get("repo")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        _issue(issues, "$.repo", "must use owner/name form")
    _validate_source(task.get("source"), repo, issues)
    _check_identity(task.get("created_by"), "$.created_by", issues)
    if task.get("priority") not in PRIORITIES:
        _issue(issues, "$.priority", "must be one of P0, P1, P2, P3")
    if not is_utc_timestamp(task.get("created_at")):
        _issue(issues, "$.created_at", "must be a valid UTC timestamp ending in Z")
    _check_string(task.get("goal"), "$.goal", issues)

    context = task.get("context")
    if not isinstance(context, list):
        _issue(issues, "$.context", "must be an array")
    else:
        for index, item in enumerate(context):
            _check_string(item, f"$.context[{index}]", issues)

    criteria = task.get("acceptance_criteria")
    criterion_ids: set[str] = set()
    if not isinstance(criteria, list) or not criteria:
        _issue(issues, "$.acceptance_criteria", "must be a non-empty array")
        criteria = []
    for index, criterion in enumerate(criteria):
        base = f"$.acceptance_criteria[{index}]"
        if not isinstance(criterion, dict):
            _issue(issues, base, "must be an object")
            continue
        if set(criterion) != {"id", "description", "verification"}:
            _issue(issues, base, "must contain only id, description, and verification")
        criterion_id = criterion.get("id")
        if _check_string(criterion_id, f"{base}.id", issues):
            if not CRITERION_ID_RE.fullmatch(criterion_id):
                _issue(issues, f"{base}.id", "must match AC-N with N greater than zero")
            if criterion_id in criterion_ids:
                _issue(issues, f"{base}.id", "must be unique within the task")
            criterion_ids.add(criterion_id)
        _check_string(criterion.get("description"), f"{base}.description", issues)
        verification = criterion.get("verification")
        if not isinstance(verification, dict):
            _issue(issues, f"{base}.verification", "must be an object")
        else:
            if set(verification) != {"type", "value"}:
                _issue(
                    issues, f"{base}.verification", "must contain only type and value"
                )
            if verification.get("type") not in VERIFICATION_TYPES:
                _issue(
                    issues,
                    f"{base}.verification.type",
                    "must be command, file, manual, or url",
                )
            _check_string(
                verification.get("value"), f"{base}.verification.value", issues
            )

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

    links = task.get("links")
    if not isinstance(links, list):
        _issue(issues, "$.links", "must be an array")
    else:
        for index, link in enumerate(links):
            _check_string(link, f"$.links[{index}]", issues)

    claim = task.get("claim")
    if claim is not None:
        if not isinstance(claim, dict):
            _issue(issues, "$.claim", "must be an object")
        else:
            expected = {"token", "github_login", "github_id", "claimed_at"}
            if set(claim) != expected:
                _issue(issues, "$.claim", "contains unexpected or missing fields")
            _check_string(claim.get("token"), "$.claim.token", issues)
            _check_string(claim.get("github_login"), "$.claim.github_login", issues)
            github_id = claim.get("github_id")
            if (
                not isinstance(github_id, int)
                or isinstance(github_id, bool)
                or github_id <= 0
            ):
                _issue(issues, "$.claim.github_id", "must be a positive integer")
            if not is_utc_timestamp(claim.get("claimed_at")):
                _issue(issues, "$.claim.claimed_at", "must be a UTC timestamp")

    completion = task.get("completion")
    if archived is False and "completion" in task:
        _issue(issues, "$.completion", "must not be present in an open task")
    if archived is True and completion is None:
        _issue(issues, "$.completion", "is required in an archived task")
    if completion is not None:
        _validate_completion(completion, criterion_ids, issues)

    return issues


def _validate_completion(
    completion: object,
    criterion_ids: set[str],
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
        "acceptance_results",
    }
    if set(completion) != expected:
        _issue(issues, "$.completion", "contains unexpected or missing fields")
    outcome = completion.get("outcome")
    if outcome not in OUTCOMES:
        _issue(issues, "$.completion.outcome", "must be done, failed, or cancelled")
    if not is_utc_timestamp(completion.get("completed_at")):
        _issue(issues, "$.completion.completed_at", "must be a UTC timestamp")
    _check_identity(completion.get("completed_by"), "$.completion.completed_by", issues)
    _check_string(completion.get("result"), "$.completion.result", issues)
    results = completion.get("acceptance_results")
    if not isinstance(results, list):
        _issue(issues, "$.completion.acceptance_results", "must be an array")
        return
    seen: set[str] = set()
    for index, result in enumerate(results):
        base = f"$.completion.acceptance_results[{index}]"
        if not isinstance(result, dict):
            _issue(issues, base, "must be an object")
            continue
        if set(result) != {"criterion_id", "status", "evidence"}:
            _issue(issues, base, "must contain criterion_id, status, and evidence")
        criterion_id = result.get("criterion_id")
        if criterion_id not in criterion_ids:
            _issue(
                issues, f"{base}.criterion_id", "must refer to an acceptance criterion"
            )
        if criterion_id in seen:
            _issue(issues, f"{base}.criterion_id", "must be unique")
        if isinstance(criterion_id, str):
            seen.add(criterion_id)
        if result.get("status") not in RESULT_STATUSES:
            _issue(issues, f"{base}.status", "must be passed, failed, or skipped")
        _check_string(result.get("evidence"), f"{base}.evidence", issues)
    if outcome == "done":
        if seen != criterion_ids:
            _issue(
                issues,
                "$.completion.acceptance_results",
                "must cover every criterion when outcome is done",
            )
        for index, result in enumerate(results):
            if isinstance(result, dict) and result.get("status") != "passed":
                _issue(
                    issues,
                    f"$.completion.acceptance_results[{index}].status",
                    "must be passed when outcome is done",
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
    if kind == "text":
        if set(source) != {"kind", "reason"}:
            _issue(issues, path, "text source must contain only kind and reason")
        _check_string(source.get("reason"), f"{path}.reason", issues)
        return
    if kind not in GITHUB_SOURCE_KINDS:
        _issue(
            issues,
            f"{path}.kind",
            "must be github_issue, github_pull_request, github_issue_fallback, or text",
        )
        return
    fallback = kind == "github_issue_fallback"
    allowed = (
        {"kind", "repo", "number", "fallback_reason"}
        if fallback
        else {"kind", "repo", "number"}
    )
    required = {"kind", "repo", "number"}
    if fallback:
        required.add("fallback_reason")
    if not required.issubset(source) or not set(source).issubset(allowed):
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
    if fallback and same_repo:
        _issue(
            issues,
            f"{path}.repo",
            "fallback Issue must be outside the execution repository",
        )
    if fallback:
        _check_string(source.get("fallback_reason"), f"{path}.fallback_reason", issues)
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
    return isinstance(value, dict) and value.get("github_id") == identity.github_id


def claim_matches_identity(value: object, identity: Identity) -> bool:
    return isinstance(value, dict) and value.get("github_id") == identity.github_id


def claim_identity(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    login = value.get("github_login")
    github_id = value.get("github_id")
    if not isinstance(login, str) or not isinstance(github_id, int):
        return None
    return {"login": login, "github_id": github_id}
