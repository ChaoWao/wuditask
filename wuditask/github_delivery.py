from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse


ISSUE_FIELDS = (
    "url,state,stateReason,assignees,closedByPullRequestsReferences,updatedAt"
)
PR_FIELDS = (
    "author,assignees,state,isDraft,mergedAt,reviewDecision,mergeStateStatus,"
    "statusCheckRollup,updatedAt"
)


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CommandResult]
Clock = Callable[[], datetime | str]


class _DeliveryQueryError(Exception):
    pass


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(clock: Clock) -> str:
    value = clock()
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def source_url(source: Mapping[str, Any]) -> str | None:
    """Derive the canonical URL without trusting a duplicated URL field."""

    repo = source.get("repo")
    number = source.get("number")
    if not isinstance(repo, str) or not repo or not _positive_int(number):
        return None
    if source.get("kind") in {"github_issue", "github_issue_fallback"}:
        return f"https://github.com/{repo}/issues/{number}"
    if source.get("kind") == "github_pull_request":
        return f"https://github.com/{repo}/pull/{number}"
    return None


def fetch_delivery(
    source: Mapping[str, Any],
    *,
    runner: Runner | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Fetch live GitHub delivery state for a canonical task source.

    Query failures are data, not exceptions: callers receive ``unavailable`` and
    can decide whether a read may be stale or a mutation must fail closed.
    """

    run = runner or _default_runner
    now = _timestamp(clock or _default_clock)
    kind = source.get("kind")
    url = source_url(source)

    if kind == "text":
        return _result(
            status="fresh",
            delivery_state="text_only",
            fetched_at=now,
            url=None,
        )
    if (
        kind
        not in {
            "github_issue",
            "github_pull_request",
            "github_issue_fallback",
        }
        or url is None
    ):
        return _unavailable(now, url, "invalid canonical source")

    repo = source["repo"]
    number = source["number"]
    try:
        if kind == "github_pull_request":
            payload = _run_json(
                run,
                [
                    "gh",
                    "pr",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    PR_FIELDS,
                ],
            )
            pr = _normalize_pr(payload, repo, number)
            return _result(
                status="fresh",
                delivery_state=_pr_delivery_state(pr),
                assignees=pr["assignees"],
                prs=[pr],
                updated_at=pr["updated_at"],
                fetched_at=now,
                url=url,
            )

        issue = _run_json(
            run,
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                ISSUE_FIELDS,
            ],
        )
        _validate_issue_url(issue.get("url"), number)
        assignees = _logins(issue.get("assignees"))
        prs = []
        seen: set[tuple[str, int]] = set()
        for reference in _closing_references(issue):
            pr_repo, pr_number = _reference_identity(reference, repo)
            identity = (pr_repo.casefold(), pr_number)
            if identity in seen:
                continue
            seen.add(identity)
            payload = _run_json(
                run,
                [
                    "gh",
                    "pr",
                    "view",
                    str(pr_number),
                    "--repo",
                    pr_repo,
                    "--json",
                    PR_FIELDS,
                ],
            )
            prs.append(_normalize_pr(payload, pr_repo, pr_number))

        updated_at = _latest_timestamp(
            issue.get("updatedAt"), *(pr["updated_at"] for pr in prs)
        )
        prs.sort(key=lambda pr: (pr["repo"].casefold(), pr["number"]))
        return _result(
            status="fresh",
            delivery_state=_issue_delivery_state(issue, assignees, prs),
            assignees=assignees,
            prs=prs,
            updated_at=updated_at,
            fetched_at=now,
            url=url,
        )
    except (OSError, TypeError, ValueError, _DeliveryQueryError) as exc:
        return _unavailable(now, url, str(exc) or exc.__class__.__name__)


def actor_eligibility(delivery: Mapping[str, Any], actor: object) -> dict[str, Any]:
    """Decide whether ``actor`` may acquire/adopt a WudiTask execution lease."""

    login = _actor_login(actor)
    state = delivery.get("delivery_state")
    owners = _delivery_owners(delivery)
    other_owners = [owner for owner in owners if owner.casefold() != login.casefold()]

    if delivery.get("status") != "fresh" or state == "unavailable":
        return _eligibility(False, "unavailable", owners)
    if state == "text_only":
        return _eligibility(True, "text_only", owners)
    if state == "verification_needed":
        return _eligibility(False, "verification_required", owners)
    if state == "cancelled":
        return _eligibility(False, "cancelled", owners)
    if other_owners:
        return _eligibility(False, "owned_elsewhere", owners)
    if owners:
        return _eligibility(True, "adopt", owners)
    return _eligibility(True, "available", owners)


def update_issue_assignee(
    source: Mapping[str, Any],
    login: str,
    *,
    add: bool,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Add or remove one assignee on an Issue source without hiding failure."""

    if (
        source.get("kind")
        not in {
            "github_issue",
            "github_issue_fallback",
        }
        or source_url(source) is None
    ):
        return {
            "status": "not_applicable",
            "changed": False,
            "error": None,
        }
    run = runner or _default_runner
    flag = "--add-assignee" if add else "--remove-assignee"
    command = [
        "gh",
        "issue",
        "edit",
        str(source["number"]),
        "--repo",
        str(source["repo"]),
        flag,
        login,
    ]
    try:
        result = run(command)
    except OSError as exc:
        return {"status": "unavailable", "changed": False, "error": str(exc)}
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "changed": False,
            "error": result.stderr.strip() or "GitHub Issue assignment failed",
        }
    return {"status": "updated", "changed": True, "error": None}


def _eligibility(eligible: bool, decision: str, owners: list[str]) -> dict[str, Any]:
    return {"eligible": eligible, "decision": decision, "owners": owners}


def _actor_login(actor: object) -> str:
    if isinstance(actor, Mapping):
        login = actor.get("login")
    else:
        login = getattr(actor, "login", None)
    if not isinstance(login, str) or not login.strip():
        raise ValueError("actor must provide a non-empty login")
    return login.strip()


def _delivery_owners(delivery: Mapping[str, Any]) -> list[str]:
    owners = _logins(delivery.get("assignees"))
    prs = delivery.get("prs")
    if isinstance(prs, list):
        for pr in prs:
            if not isinstance(pr, Mapping) or not _active_pr(pr):
                continue
            author = pr.get("author")
            if isinstance(author, str) and author:
                owners.append(author)
            owners.extend(_logins(pr.get("assignees")))
    return _unique_logins(owners)


def _run_json(runner: Runner, command: Sequence[str]) -> dict[str, Any]:
    result = runner(command)
    if result.returncode != 0:
        detail = result.stderr.strip() or "GitHub CLI command failed"
        raise _DeliveryQueryError(detail)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _DeliveryQueryError("GitHub CLI returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _DeliveryQueryError("GitHub CLI returned a non-object response")
    return payload


def _closing_references(issue: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    references = issue.get("closedByPullRequestsReferences", [])
    if references is None:
        return []
    if not isinstance(references, list):
        raise _DeliveryQueryError("issue closing pull requests are malformed")
    if not all(isinstance(reference, Mapping) for reference in references):
        raise _DeliveryQueryError("issue closing pull requests are malformed")
    return references


def _reference_identity(
    reference: Mapping[str, Any], default_repo: str
) -> tuple[str, int]:
    number = reference.get("number")
    if not _positive_int(number):
        raise _DeliveryQueryError("closing pull request has an invalid number")

    repository = reference.get("repository")
    repo: object = None
    if isinstance(repository, Mapping):
        repo = repository.get("nameWithOwner")
    if not isinstance(repo, str) or not repo:
        repo = _repo_from_pull_url(reference.get("url")) or default_repo
    return repo, number


def _repo_from_pull_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname != "github.com" or len(parts) < 4 or parts[2] != "pull":
        return None
    return f"{parts[0]}/{parts[1]}"


def _validate_issue_url(value: object, number: int) -> None:
    if not isinstance(value, str):
        raise _DeliveryQueryError("canonical Issue URL is missing")
    parsed = urlparse(value)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.hostname != "github.com"
        or len(parts) != 4
        or parts[2] != "issues"
        or parts[3] != str(number)
    ):
        raise _DeliveryQueryError(
            "canonical github_issue source does not resolve to an Issue"
        )


def _normalize_pr(payload: Mapping[str, Any], repo: str, number: int) -> dict[str, Any]:
    author = payload.get("author")
    if isinstance(author, Mapping):
        author = author.get("login")
    if not isinstance(author, str) or not author:
        author = None

    state = payload.get("state")
    if not isinstance(state, str):
        raise _DeliveryQueryError("pull request state is missing")
    is_draft = payload.get("isDraft", False)
    if not isinstance(is_draft, bool):
        raise _DeliveryQueryError("pull request draft state is malformed")

    return {
        "repo": repo,
        "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "author": author,
        "assignees": _logins(payload.get("assignees")),
        "state": state.upper(),
        "is_draft": is_draft,
        "merged_at": _optional_string(payload.get("mergedAt")),
        "review_decision": _upper_optional(payload.get("reviewDecision")),
        "merge_state_status": _upper_optional(payload.get("mergeStateStatus")),
        "checks": _normalize_checks(payload.get("statusCheckRollup")),
        "updated_at": _optional_string(payload.get("updatedAt")),
    }


def _normalize_checks(value: object) -> dict[str, int]:
    if value is None:
        entries: list[object] = []
    elif isinstance(value, list):
        entries = value
    else:
        raise _DeliveryQueryError("pull request checks are malformed")

    summary = {"total": len(entries), "successful": 0, "pending": 0, "failed": 0}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise _DeliveryQueryError("pull request check is malformed")
        status = _upper_optional(entry.get("status"))
        conclusion = _upper_optional(entry.get("conclusion"))
        state = _upper_optional(entry.get("state"))
        if status and status != "COMPLETED":
            summary["pending"] += 1
        elif conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"} or state == "SUCCESS":
            summary["successful"] += 1
        elif state in {"PENDING", "EXPECTED"}:
            summary["pending"] += 1
        else:
            summary["failed"] += 1
    return summary


def _issue_delivery_state(
    issue: Mapping[str, Any], assignees: list[str], prs: list[Mapping[str, Any]]
) -> str:
    state = _upper_optional(issue.get("state"))
    reason = _upper_optional(issue.get("stateReason"))
    if state == "CLOSED":
        return "cancelled" if reason == "NOT_PLANNED" else "verification_needed"
    if state != "OPEN":
        raise _DeliveryQueryError("issue state is missing")

    pr_states = {_pr_delivery_state(pr) for pr in prs}
    for candidate in (
        "ready_to_merge",
        "review",
        "implementing",
    ):
        if candidate in pr_states:
            return candidate
    return "assigned" if assignees else "unstarted"


def _pr_delivery_state(pr: Mapping[str, Any]) -> str:
    if pr.get("merged_at") or pr.get("state") == "MERGED":
        return "verification_needed"
    if pr.get("state") == "CLOSED":
        return "cancelled"
    if pr.get("state") != "OPEN":
        raise _DeliveryQueryError("pull request state is unsupported")
    if pr.get("is_draft"):
        return "implementing"
    if _ready_to_merge(pr):
        return "ready_to_merge"
    return "review"


def _ready_to_merge(pr: Mapping[str, Any]) -> bool:
    checks = pr.get("checks")
    if not isinstance(checks, Mapping):
        return False
    checks_ready = checks.get("pending") == 0 and checks.get("failed") == 0
    review_ready = pr.get("review_decision") not in {
        "CHANGES_REQUESTED",
        "REVIEW_REQUIRED",
    }
    return (
        checks_ready
        and review_ready
        and pr.get("merge_state_status") in {"CLEAN", "HAS_HOOKS"}
    )


def _active_pr(pr: Mapping[str, Any]) -> bool:
    return pr.get("state") == "OPEN" and not pr.get("merged_at")


def _logins(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    logins: list[str] = []
    for item in value:
        login: object
        if isinstance(item, Mapping):
            login = item.get("login")
        else:
            login = item
        if isinstance(login, str) and login:
            logins.append(login)
    return _unique_logins(logins)


def _unique_logins(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        folded = value.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(value)
    return result


def _latest_timestamp(*values: object) -> str | None:
    timestamps = [value for value in values if isinstance(value, str) and value]
    return max(timestamps, default=None)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _upper_optional(value: object) -> str | None:
    result = _optional_string(value)
    return result.upper() if result else None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _result(
    *,
    status: str,
    delivery_state: str,
    fetched_at: str,
    url: str | None,
    assignees: list[str] | None = None,
    prs: list[dict[str, Any]] | None = None,
    updated_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "delivery_state": delivery_state,
        "assignees": assignees or [],
        "prs": prs or [],
        "updated_at": updated_at,
        "fetched_at": fetched_at,
        "error": error,
        "url": url,
    }


def _unavailable(fetched_at: str, url: str | None, error: str) -> dict[str, Any]:
    return _result(
        status="unavailable",
        delivery_state="unavailable",
        fetched_at=fetched_at,
        error=error,
        url=url,
    )
