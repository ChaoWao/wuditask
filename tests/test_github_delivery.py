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
    update_issue_assignee,
)
from wuditask.model import Identity


NOW = datetime(2026, 7, 16, 10, 30, tzinfo=timezone.utc)


def completed(
    payload: object | None = None, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        [],
        returncode,
        stdout=json.dumps(payload) if payload is not None else "",
        stderr=stderr,
    )


class ScriptedRunner:
    def __init__(self, *results: subprocess.CompletedProcess[str]) -> None:
        self.results = list(results)
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if not self.results:
            raise AssertionError(f"unexpected command: {command}")
        return self.results.pop(0)


def issue(
    *,
    state: str = "OPEN",
    reason: str | None = None,
    assignees: list[str] | None = None,
    closing_prs: list[dict[str, object]] | None = None,
    repo: str = "acme/service",
    number: int = 12,
) -> dict[str, object]:
    return {
        "url": f"https://github.com/{repo}/issues/{number}",
        "state": state,
        "stateReason": reason,
        "assignees": [{"login": login} for login in assignees or []],
        "closedByPullRequestsReferences": closing_prs or [],
        "updatedAt": "2026-07-16T09:00:00Z",
    }


def pr(
    *,
    author: str = "alice",
    state: str = "OPEN",
    draft: bool = False,
    merged_at: str | None = None,
    review: str | None = "REVIEW_REQUIRED",
    merge_state: str = "BLOCKED",
    checks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "author": {"login": author},
        "assignees": [],
        "state": state,
        "isDraft": draft,
        "mergedAt": merged_at,
        "reviewDecision": review,
        "mergeStateStatus": merge_state,
        "statusCheckRollup": checks or [],
        "updatedAt": "2026-07-16T10:00:00Z",
    }


class GithubDeliveryTests(unittest.TestCase):
    def test_text_source_is_fresh_without_calling_github(self) -> None:
        runner = ScriptedRunner()

        result = fetch_delivery(
            {"kind": "text", "reason": "Local operational work"},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("fresh", result["status"])
        self.assertEqual("text_only", result["delivery_state"])
        self.assertEqual(
            {
                "status",
                "delivery_state",
                "assignees",
                "prs",
                "updated_at",
                "fetched_at",
                "error",
                "url",
            },
            set(result),
        )
        self.assertEqual("2026-07-16T10:30:00Z", result["fetched_at"])
        self.assertEqual([], runner.commands)
        self.assertIsNone(result["url"])
        self.assertEqual(
            {"eligible": True, "decision": "text_only", "owners": []},
            actor_eligibility(result, Identity("alice", 1001)),
        )

    def test_urls_are_derived_from_structured_source(self) -> None:
        self.assertEqual(
            "https://github.com/acme/service/issues/12",
            source_url(
                {
                    "kind": "github_issue_fallback",
                    "repo": "acme/service",
                    "number": 12,
                    "fallback_reason": "Issue access unavailable in execution repo",
                }
            ),
        )
        self.assertEqual(
            "https://github.com/acme/service/pull/13",
            source_url(
                {"kind": "github_pull_request", "repo": "acme/service", "number": 13}
            ),
        )

    def test_unassigned_issue_without_closing_pr_is_unstarted(self) -> None:
        runner = ScriptedRunner(completed(issue()))

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("unstarted", result["delivery_state"])
        self.assertEqual([], result["assignees"])
        self.assertEqual([], result["prs"])
        self.assertIn("closedByPullRequestsReferences", runner.commands[0][-1])

    def test_assigned_issue_reports_assignees(self) -> None:
        runner = ScriptedRunner(completed(issue(assignees=["alice", "bob"])))

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("assigned", result["delivery_state"])
        self.assertEqual(["alice", "bob"], result["assignees"])

    def test_only_closing_pr_references_are_queried_and_draft_is_implementing(
        self,
    ) -> None:
        runner = ScriptedRunner(
            completed(
                issue(
                    repo="acme/hub",
                    closing_prs=[
                        {
                            "number": 27,
                            "url": "https://github.com/acme/service/pull/27",
                        }
                    ],
                )
            ),
            completed(pr(draft=True)),
        )

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("implementing", result["delivery_state"])
        self.assertEqual(27, result["prs"][0]["number"])
        self.assertEqual("alice", result["prs"][0]["author"])
        self.assertEqual("pr", runner.commands[1][1])
        self.assertEqual("27", runner.commands[1][3])

    def test_cross_repository_closing_pr_uses_reference_repository(self) -> None:
        runner = ScriptedRunner(
            completed(
                issue(
                    closing_prs=[
                        {
                            "number": 9,
                            "repository": {"nameWithOwner": "acme/worker"},
                        }
                    ]
                )
            ),
            completed(pr()),
        )

        result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/hub", "number": 12},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("acme/worker", result["prs"][0]["repo"])
        self.assertEqual("acme/worker", runner.commands[1][5])

    def test_open_pr_moves_from_review_to_ready_to_merge(self) -> None:
        review_runner = ScriptedRunner(completed(pr()))
        review = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=review_runner,
            clock=lambda: NOW,
        )

        ready_runner = ScriptedRunner(
            completed(
                pr(
                    review="APPROVED",
                    merge_state="CLEAN",
                    checks=[
                        {"status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"state": "SUCCESS"},
                    ],
                )
            )
        )
        ready = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=ready_runner,
            clock=lambda: NOW,
        )

        self.assertEqual("review", review["delivery_state"])
        self.assertEqual("ready_to_merge", ready["delivery_state"])
        self.assertEqual(
            {"total": 2, "successful": 2, "pending": 0, "failed": 0},
            ready["prs"][0]["checks"],
        )

    def test_pending_check_keeps_approved_pr_in_review(self) -> None:
        runner = ScriptedRunner(
            completed(
                pr(
                    review="APPROVED",
                    merge_state="CLEAN",
                    checks=[{"status": "IN_PROGRESS", "conclusion": None}],
                )
            )
        )

        result = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=runner,
            clock=lambda: NOW,
        )

        self.assertEqual("review", result["delivery_state"])
        self.assertEqual(1, result["prs"][0]["checks"]["pending"])

    def test_merged_pr_and_completed_issue_need_wuditask_verification(self) -> None:
        merged_runner = ScriptedRunner(
            completed(
                pr(
                    state="MERGED",
                    merged_at="2026-07-16T10:15:00Z",
                    review="APPROVED",
                    merge_state="CLEAN",
                )
            )
        )
        merged = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=merged_runner,
            clock=lambda: NOW,
        )
        closed_runner = ScriptedRunner(
            completed(issue(state="CLOSED", reason="COMPLETED"))
        )
        closed = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=closed_runner,
            clock=lambda: NOW,
        )

        self.assertEqual("verification_needed", merged["delivery_state"])
        self.assertEqual("verification_needed", closed["delivery_state"])

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

    def test_not_planned_issue_and_closed_unmerged_pr_are_cancelled(self) -> None:
        issue_runner = ScriptedRunner(
            completed(issue(state="CLOSED", reason="NOT_PLANNED"))
        )
        issue_result = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=issue_runner,
            clock=lambda: NOW,
        )
        pr_runner = ScriptedRunner(completed(pr(state="CLOSED")))
        pr_result = fetch_delivery(
            {"kind": "github_pull_request", "repo": "acme/service", "number": 27},
            runner=pr_runner,
            clock=lambda: NOW,
        )

        self.assertEqual("cancelled", issue_result["delivery_state"])
        self.assertEqual("cancelled", pr_result["delivery_state"])

    def test_command_failure_and_invalid_json_are_unavailable(self) -> None:
        failed_runner = ScriptedRunner(completed(returncode=1, stderr="not logged in"))
        failed = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=failed_runner,
            clock=lambda: NOW,
        )
        invalid_runner = ScriptedRunner(
            subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        )
        invalid = fetch_delivery(
            {"kind": "github_issue", "repo": "acme/service", "number": 12},
            runner=invalid_runner,
            clock=lambda: NOW,
        )

        self.assertEqual("unavailable", failed["status"])
        self.assertEqual("unavailable", failed["delivery_state"])
        self.assertEqual("not logged in", failed["error"])
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

    def test_actor_eligibility_is_conservative_about_other_github_owners(self) -> None:
        base = {
            "status": "fresh",
            "delivery_state": "review",
            "assignees": ["alice"],
            "prs": [
                {
                    "state": "OPEN",
                    "merged_at": None,
                    "author": "alice",
                }
            ],
        }

        self.assertEqual(
            {"eligible": True, "decision": "adopt", "owners": ["alice"]},
            actor_eligibility(base, Identity("alice", 1001)),
        )
        self.assertEqual(
            {
                "eligible": False,
                "decision": "owned_elsewhere",
                "owners": ["alice"],
            },
            actor_eligibility(base, {"login": "bob", "github_id": 1002}),
        )

    def test_actor_cannot_execute_completed_or_unknown_delivery(self) -> None:
        verification = {
            "status": "fresh",
            "delivery_state": "verification_needed",
            "assignees": ["alice"],
            "prs": [],
        }
        unavailable = {
            "status": "unavailable",
            "delivery_state": "unavailable",
            "assignees": [],
            "prs": [],
        }

        self.assertEqual(
            "verification_required",
            actor_eligibility(verification, Identity("alice", 1001))["decision"],
        )
        self.assertEqual(
            {"eligible": False, "decision": "unavailable", "owners": []},
            actor_eligibility(unavailable, Identity("alice", 1001)),
        )

    def test_issue_assignment_reports_success_and_failure(self) -> None:
        source = {"kind": "github_issue", "repo": "acme/service", "number": 12}
        success_runner = ScriptedRunner(completed({}))
        success = update_issue_assignee(
            source, "alice", add=True, runner=success_runner
        )
        failed = update_issue_assignee(
            source,
            "alice",
            add=False,
            runner=ScriptedRunner(completed(returncode=1, stderr="forbidden")),
        )

        self.assertEqual("updated", success["status"])
        self.assertIn("--add-assignee", success_runner.commands[0])
        self.assertEqual("unavailable", failed["status"])
        self.assertEqual("forbidden", failed["error"])


if __name__ == "__main__":
    unittest.main()
