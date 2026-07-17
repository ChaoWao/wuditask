from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask.dependencies import dependency_report
from wuditask.errors import WudiTaskError
from wuditask.model import Identity
from wuditask.util import atomic_write_json
from wuditask.workflow import archive_task, claim_task, release_task

from tests.helpers import ACTOR, OTHER_ACTOR, add_task, make_repository

DEPENDENCY_ID = "WDT-20260711T120000Z-111111"
PARENT_ID = "WDT-20260711T120001Z-222222"


class WorkflowTests(unittest.TestCase):
    @staticmethod
    def github_delivery(
        state: str,
        *,
        assignees: list[str] | None = None,
        author: str | None = None,
    ) -> dict[str, object]:
        prs = []
        if author:
            prs.append({"state": "OPEN", "merged_at": None, "author": author})
        return {
            "status": "fresh",
            "delivery_state": state,
            "assignees": assignees or [],
            "prs": prs,
            "updated_at": None,
            "fetched_at": "2026-07-16T10:00:00Z",
            "error": None,
            "url": "https://github.com/acme/service/issues/42",
        }

    def test_github_delivery_owner_blocks_another_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, PARENT_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{PARENT_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("review", assignees=["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    claim_task(repository, ACTOR, task_id=PARENT_ID)

        self.assertEqual("delivery_owned_elsewhere", raised.exception.code)

    def test_current_github_assignee_can_adopt_the_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, PARENT_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{PARENT_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("assigned", assignees=["alice"]),
            ):
                claimed = claim_task(repository, ACTOR, task_id=PARENT_ID)

        self.assertEqual("adopt", claimed["delivery_eligibility"]["decision"])

    def test_existing_claim_rechecks_github_ownership_and_availability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, PARENT_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{PARENT_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("assigned", assignees=["alice"]),
            ):
                claim_task(repository, ACTOR, task_id=PARENT_ID)

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("assigned", assignees=["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as reassigned:
                    claim_task(repository, ACTOR, task_id=PARENT_ID)
            self.assertEqual("delivery_owned_elsewhere", reassigned.exception.code)

            unavailable = self.github_delivery("unavailable")
            unavailable["status"] = "unavailable"
            unavailable["error"] = "API unavailable"
            with patch("wuditask.workflow.fetch_delivery", return_value=unavailable):
                with self.assertRaises(WudiTaskError) as unknown:
                    claim_task(repository, ACTOR, task_id=PARENT_ID)
            self.assertEqual("github_delivery_unavailable", unknown.exception.code)

    def test_done_archive_requires_completed_github_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, PARENT_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{PARENT_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("assigned", assignees=["alice"]),
            ):
                claim_task(repository, ACTOR, task_id=PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("review", assignees=["alice"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="done",
                        result="Implemented.",
                        evidence={"AC-1": "Tests passed."},
                    )
            self.assertEqual("github_delivery_incomplete", raised.exception.code)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("verification_needed"),
            ):
                archived = archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="done",
                    result="Implemented and verified.",
                    evidence={"AC-1": "Tests passed."},
                    now="2026-07-11T13:00:00Z",
                )

        self.assertTrue(archived["confirmed"])

    def test_add_claim_archive_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)

            claimed = claim_task(repository, ACTOR, repo="acme/service")
            self.assertEqual(PARENT_ID, claimed["task_id"])
            self.assertTrue(claimed["confirmed"])
            self.assertEqual("alice", claimed["task"]["claim"]["github_login"])

            retried = claim_task(repository, ACTOR, task_id=PARENT_ID)
            self.assertTrue(retried["already_claimed"])
            self.assertTrue(retried["dependency_check"]["ready"])

            with self.assertRaises(WudiTaskError) as missing:
                archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="done",
                    result="Implemented.",
                    evidence={},
                )
            self.assertEqual("insufficient_archive_evidence", missing.exception.code)

            archived = archive_task(
                repository,
                ACTOR,
                PARENT_ID,
                outcome="done",
                result="Implemented and verified.",
                evidence={"AC-1": "python3 -m unittest: 8 tests passed"},
                now="2026-07-11T13:00:00Z",
            )
            self.assertTrue(archived["confirmed"])
            index = repository.load_index()
            self.assertNotIn(PARENT_ID, index.open)
            self.assertIn(PARENT_ID, index.archived)
            self.assertEqual(
                "passed",
                index.archived[PARENT_ID].task["completion"]["acceptance_results"][0][
                    "status"
                ],
            )

    def test_dependency_blocks_until_done_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, DEPENDENCY_ID, title="Dependency")
            add_task(
                repository,
                PARENT_ID,
                title="Parent",
                dependencies=[DEPENDENCY_ID],
            )

            report = dependency_report(repository.load_index(), PARENT_ID)["task"]
            self.assertFalse(report["ready"])
            self.assertEqual(
                "dependency is still open", report["dependencies"][0]["reason"]
            )

            with self.assertRaises(WudiTaskError) as blocked:
                claim_task(repository, ACTOR, task_id=PARENT_ID)
            self.assertEqual("no_ready_task", blocked.exception.code)

            claim_task(repository, ACTOR, task_id=DEPENDENCY_ID)
            archive_task(
                repository,
                ACTOR,
                DEPENDENCY_ID,
                outcome="done",
                result="Dependency complete.",
                evidence={"AC-1": "Regression command passed."},
                now="2026-07-11T13:00:00Z",
            )

            report = dependency_report(repository.load_index(), PARENT_ID)["task"]
            self.assertTrue(report["ready"])
            self.assertEqual("acme/service", report["dependencies"][0]["repo"])
            self.assertEqual(1, len(report["dependencies"][0]["acceptance_criteria"]))
            claimed = claim_task(repository, OTHER_ACTOR, task_id=PARENT_ID)
            self.assertEqual("bob", claimed["task"]["claim"]["github_login"])

    def test_failed_dependency_never_unblocks_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, DEPENDENCY_ID, title="Dependency")
            add_task(repository, PARENT_ID, dependencies=[DEPENDENCY_ID])
            claim_task(repository, ACTOR, task_id=DEPENDENCY_ID)
            archive_task(
                repository,
                ACTOR,
                DEPENDENCY_ID,
                outcome="failed",
                result="Upstream API cannot meet the requirement.",
                evidence={},
                now="2026-07-11T13:00:00Z",
            )
            report = dependency_report(repository.load_index(), PARENT_ID)["task"]
            self.assertFalse(report["ready"])
            self.assertIn("failed", report["dependencies"][0]["reason"])

    def test_blocked_unclaimed_task_can_be_cancelled_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, DEPENDENCY_ID, title="Dependency")
            add_task(
                repository,
                PARENT_ID,
                title="Parent",
                dependencies=[DEPENDENCY_ID],
            )

            archived = archive_task(
                repository,
                ACTOR,
                PARENT_ID,
                outcome="cancelled",
                result="No longer required.",
                evidence={},
                now="2026-07-11T13:00:00Z",
            )

            self.assertTrue(archived["confirmed"])
            completion = repository.load_index().archived[PARENT_ID].task["completion"]
            self.assertEqual("cancelled", completion["outcome"])

    def test_github_backed_terminal_archive_requires_not_planned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, PARENT_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{PARENT_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("assigned", assignees=["alice"]),
            ):
                with self.assertRaises(WudiTaskError) as active:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="cancelled",
                        result="Requirement withdrawn.",
                        evidence={},
                    )
            self.assertEqual("github_delivery_not_terminal", active.exception.code)

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=self.github_delivery("cancelled"),
            ):
                archived = archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="cancelled",
                    result="Requirement withdrawn.",
                    evidence={},
                )

        self.assertTrue(archived["confirmed"])

    def test_cycle_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            first = add_task(repository, DEPENDENCY_ID, title="First")
            add_task(
                repository, PARENT_ID, title="Second", dependencies=[DEPENDENCY_ID]
            )
            first["dependencies"] = [PARENT_ID]
            atomic_write_json(repository.open_dir / f"{DEPENDENCY_ID}.json", first)

            report = dependency_report(repository.load_index(), DEPENDENCY_ID)["task"]
            self.assertEqual(
                [DEPENDENCY_ID, PARENT_ID, DEPENDENCY_ID],
                report["cycle"],
            )
            self.assertFalse(report["ready"])

    def test_release_requires_current_human_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            claim_task(repository, ACTOR, task_id=PARENT_ID)
            with self.assertRaises(WudiTaskError) as mismatch:
                release_task(
                    repository, OTHER_ACTOR, PARENT_ID, reason="Cannot continue."
                )
            self.assertEqual("claim_holder_mismatch", mismatch.exception.code)
            released = release_task(
                repository,
                ACTOR,
                PARENT_ID,
                reason="Waiting for clarification.",
            )
            self.assertTrue(released["changed"])
            task = repository.load_index().open[PARENT_ID].task
            self.assertIsNone(task["claim"])

    def test_github_id_survives_login_rename(self) -> None:
        renamed = Identity("alice-renamed", ACTOR.github_id)
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            claim_task(repository, ACTOR, task_id=PARENT_ID)

            resumed = claim_task(repository, renamed, task_id=PARENT_ID)
            released = release_task(
                repository,
                renamed,
                PARENT_ID,
                reason="Resume under the renamed GitHub login, then release.",
            )

        self.assertTrue(resumed["already_claimed"])
        self.assertTrue(resumed["claim_login_refreshed"])
        self.assertEqual("alice-renamed", resumed["task"]["claim"]["github_login"])
        self.assertTrue(released["confirmed"])

    def test_release_preflight_token_prevents_aba_and_unclaimed_races(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            first = claim_task(repository, ACTOR, task_id=PARENT_ID)
            old_token = first["task"]["claim"]["token"]

            with self.assertRaises(WudiTaskError) as expected_unclaimed:
                release_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    reason="Stale unclaimed preflight.",
                    expected_unclaimed=True,
                )
            self.assertEqual("claim_state_changed", expected_unclaimed.exception.code)

            release_task(
                repository,
                ACTOR,
                PARENT_ID,
                reason="End the first lease.",
            )
            second = claim_task(repository, ACTOR, task_id=PARENT_ID)
            self.assertNotEqual(old_token, second["task"]["claim"]["token"])
            with self.assertRaises(WudiTaskError) as stale_token:
                release_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    reason="Stale lease release.",
                    expected_claim_token=old_token,
                )
            self.assertEqual("claim_token_mismatch", stale_token.exception.code)


if __name__ == "__main__":
    unittest.main()
