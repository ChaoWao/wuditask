from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wuditask.dependencies import dependency_report
from wuditask.errors import WudiTaskError
from wuditask.util import atomic_write_json
from wuditask.workflow import archive_task, claim_task, release_task

from tests.helpers import ACTOR, OTHER_ACTOR, add_task, make_repository

DEPENDENCY_ID = "WDT-20260711T120000Z-111111"
PARENT_ID = "WDT-20260711T120001Z-222222"


class WorkflowTests(unittest.TestCase):
    def test_add_claim_archive_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)

            claimed = claim_task(repository, ACTOR, repo="acme/service")
            self.assertEqual(PARENT_ID, claimed["task_id"])
            self.assertTrue(claimed["confirmed"])
            self.assertEqual("alice", claimed["task"]["owner"]["login"])

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
            self.assertEqual("bob", claimed["task"]["owner"]["login"])

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
            self.assertEqual("owner_mismatch", mismatch.exception.code)
            released = release_task(
                repository,
                ACTOR,
                PARENT_ID,
                reason="Waiting for clarification.",
            )
            self.assertTrue(released["changed"])
            task = repository.load_index().open[PARENT_ID].task
            self.assertIsNone(task["owner"])
            self.assertIsNone(task["claim"])


if __name__ == "__main__":
    unittest.main()
