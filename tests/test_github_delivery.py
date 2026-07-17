from __future__ import annotations

import json
import subprocess
import unittest
from collections.abc import Sequence
from datetime import datetime, timezone

from wuditask.github_delivery import (
    actor_eligibility,
    fetch_delivery,
    source_url,
    update_source_assignee,
)
from wuditask.model import Identity

NOW = datetime(2026, 7, 16, 10, 30, tzinfo=timezone.utc)


def completed(payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")


class ScriptedRunner:
    def __init__(self, *results: subprocess.CompletedProcess[str]) -> None:
        self.results = list(results)
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        return self.results.pop(0)


def issue(
    *,
    title: str = "Coordinate delivery",
    body: str = "Canonical issue body",
    state: str = "OPEN",
    reason: str | None = None,
    assignees: list[str] | None = None,
    closing_prs: list[dict[str, object]] | None = None,
    repo: str = "acme/service",
    number: int = 12,
) -> dict[str, object]:
    return {
        "title": title,
        "body": body,
        "url": f"https://github.com/{repo}/issues/{number}",
        "state": state,
        "stateReason": reason,
        "assignees": [{"login": login} for login in assignees or []],
        "closedByPullRequestsReferences": closing_prs or [],
        "updatedAt": "2026-07-16T09:00:00Z",
    }


def pr(
    *,
    title: str = "Implement delivery",
    body: str = "Canonical PR body",
    author: str = "alice",
    assignees: list[str] | None = None,
    state: str = "OPEN",
    draft: bool = False,
    merged_at: str | None = None,
    review: str | None = "REVIEW_REQUIRED",
    merge_state: str = "BLOCKED",
    checks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "title": title,
        "body": body,
        "author": {"login": author},
        "assignees": [{"login": login} for login in assignees or []],
        "state": state,
        "isDraft": draft,
        "mergedAt": merged_at,
        "reviewDecision": review,
        "mergeStateStatus": merge_state,
        "statusCheckRollup": checks or [],
        "updatedAt": "2026-07-16T10:00:00Z",
    }


class GithubDeliveryTests(unittest.TestCase):
    def test_assignment_helper_supports_issue_and_pull_request_sources(self) -> None:
        for kind, noun in (("github_issue", "issue"), ("github_pull_request", "pr")):
            with self.subTest(kind=kind):
                runner = ScriptedRunner(completed({}))
                result = update_source_assignee(
                    {"kind": kind, "repo": "acme/service", "number": 12},
                    "alice",
                    add=True,
                    runner=runner,
                )
                self.assertEqual("updated", result["status"])
                self.assertEqual(noun, runner.commands[0][1])
                self.assertIn("--add-assignee", runner.commands[0])

    def test_source_url_supports_issue_pr_and_fallback_only(self) -> None:
        self.assertEqual(
            "https://github.com/acme/service/issues/12",
            source_url({"kind": "github_issue", "repo": "acme/service", "number": 12}),
        )
        self.assertEqual(
            "https://github.com/acme/service/pull/13",
            source_url({"kind": "github_pull_request", "repo": "acme/service", "number": 13}),
        )
        self.assertIsNone(source_url({"kind": "text", "reason": "removed"}))

    def test_issue_delivery_includes_canonical_content_and_all_live_owners(self) -> None:
        runner = ScriptedRunner(
            completed(
                issue(
                    assignees=["Alice", "bob"],
                    closing_prs=[
                        {"number": 27, "url": "https://github.com/acme/service/pull/27"},
                        {"number": 28, "url": "https://github.com/acme/service/pull/28"},
                        {"number": 29, "url": "https://github.com/acme/service/pull/29"},
                    ],
                )
            ),
            completed(pr(author="carol", assignees=["ignored-pr-assignee"])),
            completed(pr(author="dave", state="CLOSED")),
            completed(pr(author="erin", state="MERGED", merged_at="2026-07-16T10:00:00Z")),
        )
        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("Coordinate delivery", result["title"])
        self.assertEqual("Canonical issue body", result["body"])
        self.assertEqual(["Alice", "bob", "carol", "erin"], result["owners"])
        self.assertNotIn("dave", result["owners"])
        self.assertNotIn("ignored-pr-assignee", result["owners"])
        self.assertIn("title", runner.commands[0][-1])
        self.assertIn("body", runner.commands[0][-1])

    def test_canonical_pr_owners_are_author_and_assignees(self) -> None:
        result = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=ScriptedRunner(completed(pr(author="alice", assignees=["Bob", "alice"]))),
            clock=lambda: NOW,
        )

        self.assertEqual("Implement delivery", result["title"])
        self.assertEqual("Canonical PR body", result["body"])
        self.assertEqual(["alice", "Bob"], result["owners"])
        self.assertEqual("review", result["delivery_state"])

    def test_actor_eligibility_uses_precomputed_owners_without_excluding_coowners(self) -> None:
        delivery = {
            "status": "fresh",
            "delivery_state": "review",
            "owners": ["alice", "bob"],
        }
        self.assertEqual(
            {"eligible": True, "decision": "owner", "owners": ["alice", "bob"]},
            actor_eligibility(delivery, Identity("Alice")),
        )
        self.assertEqual(
            {"eligible": False, "decision": "owner_required", "owners": ["alice", "bob"]},
            actor_eligibility(delivery, Identity("carol")),
        )

    def test_terminal_and_unavailable_deliveries_are_not_executable(self) -> None:
        for state, decision in (
            ("verification_needed", "verification_required"),
            ("cancelled", "cancelled"),
            ("unavailable", "unavailable"),
        ):
            with self.subTest(state=state):
                result = actor_eligibility(
                    {
                        "status": "unavailable" if state == "unavailable" else "fresh",
                        "delivery_state": state,
                        "owners": ["alice"],
                    },
                    Identity("alice"),
                )
                self.assertFalse(result["eligible"])
                self.assertEqual(decision, result["decision"])

    def test_unavailable_delivery_keeps_ownership_unknown(self) -> None:
        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=ScriptedRunner(
                subprocess.CompletedProcess([], 1, stdout="", stderr="not found")
            ),
            clock=lambda: NOW,
        )

        self.assertEqual("unavailable", result["status"])
        self.assertIsNone(result["owners"])
        self.assertIsNone(result["assignees"])

    def test_delivery_states_remain_derived_from_live_github(self) -> None:
        merged = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=ScriptedRunner(completed(pr(state="MERGED", merged_at="2026-07-16T10:00:00Z"))),
            clock=lambda: NOW,
        )
        cancelled = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=ScriptedRunner(completed(issue(state="CLOSED", reason="NOT_PLANNED"))),
            clock=lambda: NOW,
        )
        self.assertEqual("verification_needed", merged["delivery_state"])
        self.assertEqual("cancelled", cancelled["delivery_state"])

    def test_cross_repository_closing_pr_uses_reference_repository(self) -> None:
        runner = ScriptedRunner(
            completed(
                issue(
                    repo="acme/hub",
                    closing_prs=[
                        {
                            "number": 9,
                            "repository": {"nameWithOwner": "acme/worker"},
                        }
                    ],
                )
            ),
            completed(pr(author="carol", draft=True)),
        )

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/hub", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("implementing", result["delivery_state"])
        self.assertEqual("acme/worker", result["prs"][0]["repo"])
        self.assertEqual(["carol"], result["owners"])
        self.assertEqual("acme/worker", runner.commands[1][5])

    def test_reopened_issue_is_active_even_when_a_closing_pr_is_merged(self) -> None:
        runner = ScriptedRunner(
            completed(
                issue(
                    state="OPEN",
                    assignees=["alice"],
                    closing_prs=[
                        {
                            "number": 27,
                            "url": "https://github.com/acme/service/pull/27",
                        }
                    ],
                )
            ),
            completed(
                pr(
                    author="bob",
                    state="MERGED",
                    merged_at="2026-07-16T10:15:00Z",
                    review="APPROVED",
                    merge_state="CLEAN",
                )
            ),
        )

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("assigned", result["delivery_state"])
        self.assertEqual(["alice", "bob"], result["owners"])

    def test_command_failure_and_invalid_json_are_unavailable(self) -> None:
        failed = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=ScriptedRunner(
                subprocess.CompletedProcess(
                    [],
                    1,
                    stdout="",
                    stderr="not logged in",
                )
            ),
            clock=lambda: NOW,
        )
        invalid = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=ScriptedRunner(
                subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
            ),
            clock=lambda: NOW,
        )

        self.assertEqual("unavailable", failed["status"])
        self.assertEqual("not logged in", failed["error"])
        self.assertEqual("unavailable", invalid["status"])
        self.assertEqual("GitHub CLI returned invalid JSON", invalid["error"])

    def test_issue_source_rejects_a_pull_request_number(self) -> None:
        payload = issue()
        payload["url"] = "https://github.com/acme/service/pull/12"

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=ScriptedRunner(completed(payload)),
            clock=lambda: NOW,
        )

        self.assertEqual("unavailable", result["delivery_state"])
        self.assertIn("does not resolve to an Issue", result["error"])


if __name__ == "__main__":
    unittest.main()
