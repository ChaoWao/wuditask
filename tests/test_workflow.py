from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask.errors import WudiTaskError
from wuditask.workflow import archive_task, release_agent, start_agent

from tests.helpers import (
    ACTOR,
    OTHER_ACTOR,
    OTHER_RUN_ID,
    RUN_ID,
    add_task,
    make_repository,
)

DEPENDENCY_ID = "WDT-20260711T120000Z-111111"
PARENT_ID = "WDT-20260711T120001Z-222222"


def delivery(state: str, owners: list[str]) -> dict[str, object]:
    return {
        "status": "unavailable" if state == "unavailable" else "fresh",
        "delivery_state": state,
        "title": "Canonical task",
        "body": "Canonical body",
        "owners": owners,
        "assignees": owners,
        "prs": [],
        "updated_at": "2026-07-16T09:00:00Z",
        "fetched_at": "2026-07-16T10:00:00Z",
        "error": "API unavailable" if state == "unavailable" else None,
        "url": "https://github.com/acme/service/issues/12",
    }


class WorkflowTests(unittest.TestCase):
    def test_start_requires_live_owner_and_ready_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", ["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)

        self.assertEqual("delivery_owner_required", raised.exception.code)

    def test_start_is_idempotent_per_run_and_conflicts_per_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", ["alice"]),
            ):
                first = start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                retry = start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                with self.assertRaises(WudiTaskError) as raised:
                    start_agent(
                        repository,
                        ACTOR,
                        task_id=PARENT_ID,
                        run_id=OTHER_RUN_ID,
                    )

        self.assertTrue(first["changed"])
        self.assertTrue(first["agent_started"])
        self.assertFalse(retry["changed"])
        self.assertTrue(retry["already_active"])
        self.assertEqual("active_agent_conflict", raised.exception.code)

    def test_different_live_owners_may_be_active_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("review", ["alice", "bob"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                result = start_agent(
                    repository,
                    OTHER_ACTOR,
                    task_id=PARENT_ID,
                    run_id=OTHER_RUN_ID,
                )

        self.assertEqual(
            [
                {"login": "alice", "run_id": RUN_ID},
                {"login": "bob", "run_id": OTHER_RUN_ID},
            ],
            result["task"]["active_agents"],
        )

    def test_start_rejects_blocked_dependency_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, DEPENDENCY_ID)
            add_task(repository, PARENT_ID, dependencies=[DEPENDENCY_ID])
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", ["alice"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)

            self.assertEqual([], repository.load_index().open[PARENT_ID].task["active_agents"])
        self.assertEqual("dependency_blocked", raised.exception.code)

    def test_release_requires_exact_actor_run_and_removes_only_that_actor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("review", ["alice", "bob"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                start_agent(repository, OTHER_ACTOR, task_id=PARENT_ID, run_id=OTHER_RUN_ID)
            with self.assertRaises(WudiTaskError) as mismatch:
                release_agent(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    run_id=OTHER_RUN_ID,
                    reason="Wrong run",
                )
            released = release_agent(
                repository,
                ACTOR,
                PARENT_ID,
                run_id=RUN_ID,
                reason="Stop this agent",
            )

        self.assertEqual("active_agent_run_mismatch", mismatch.exception.code)
        self.assertEqual(
            [{"login": "bob", "run_id": OTHER_RUN_ID}],
            released["task"]["active_agents"],
        )

    def test_archive_done_requires_terminal_delivery_active_run_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("review", ["alice", "bob"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                start_agent(repository, OTHER_ACTOR, task_id=PARENT_ID, run_id=OTHER_RUN_ID)

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("verification_needed", ["alice", "bob"]),
            ):
                with self.assertRaises(WudiTaskError) as no_evidence:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="done",
                        result="Implemented.",
                        evidence=[],
                        run_id=RUN_ID,
                    )
                archived = archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="done",
                    result="Implemented.",
                    evidence=["Tests passed", "PR merged"],
                    run_id=RUN_ID,
                    now="2026-07-11T13:00:00Z",
                )

        self.assertEqual("insufficient_archive_evidence", no_evidence.exception.code)
        self.assertEqual([], archived["task"]["active_agents"])
        self.assertEqual(
            [
                {"login": "alice", "run_id": RUN_ID},
                {"login": "bob", "run_id": OTHER_RUN_ID},
            ],
            archived["task"]["completion"]["participants"],
        )
        self.assertEqual("alice", archived["task"]["completion"]["completed_by"])
        self.assertEqual(["Tests passed", "PR merged"], archived["task"]["completion"]["evidence"])

    def test_archive_rejects_wrong_run_and_nonterminal_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", ["alice"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                with self.assertRaises(WudiTaskError) as active:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="done",
                        result="Done",
                        evidence=["test"],
                        run_id=RUN_ID,
                    )
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("verification_needed", ["alice"]),
            ):
                with self.assertRaises(WudiTaskError) as mismatch:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="done",
                        result="Done",
                        evidence=["test"],
                        run_id=OTHER_RUN_ID,
                    )

        self.assertEqual("github_delivery_incomplete", active.exception.code)
        self.assertEqual("active_agent_run_mismatch", mismatch.exception.code)

    def test_cancelled_archive_allows_blocked_unclaimed_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, DEPENDENCY_ID)
            add_task(repository, PARENT_ID, dependencies=[DEPENDENCY_ID])

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled", []),
            ):
                archived = archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="cancelled",
                    result="No longer planned.",
                    evidence=[],
                    run_id=None,
                    now="2026-07-11T13:00:00Z",
                )

        self.assertTrue(archived["confirmed"])
        self.assertIsNone(archived["run_id"])
        self.assertEqual([], archived["task"]["active_agents"])
        self.assertEqual([], archived["task"]["completion"]["participants"])
        self.assertEqual("alice", archived["task"]["completion"]["completed_by"])

    def test_released_task_allows_creator_terminal_failure_or_cancellation(self) -> None:
        cases = (("failed", "verification_needed"), ("cancelled", "cancelled"))
        for outcome, terminal_state in cases:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                repository = make_repository(Path(temporary))
                add_task(repository, PARENT_ID)
                with patch(
                    "wuditask.workflow.fetch_delivery",
                    return_value=delivery("assigned", ["alice"]),
                ):
                    start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                release_agent(repository, ACTOR, PARENT_ID, run_id=RUN_ID)

                with patch(
                    "wuditask.workflow.fetch_delivery",
                    return_value=delivery(terminal_state, []),
                ):
                    archived = archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome=outcome,
                        result=f"Terminal {outcome} result.",
                        evidence=[],
                        run_id=None,
                    )
                    retry = archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome=outcome,
                        result=f"Terminal {outcome} result.",
                        evidence=[],
                        run_id=None,
                    )

                self.assertEqual([], archived["task"]["completion"]["participants"])
                self.assertTrue(retry["already_archived"])
                self.assertFalse(retry["changed"])

    def test_terminal_archive_with_active_agents_requires_matching_actor_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", ["alice"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled", []),
            ):
                with self.assertRaises(WudiTaskError) as missing:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="cancelled",
                        result="No longer planned.",
                        evidence=[],
                        run_id=None,
                    )
                with self.assertRaises(WudiTaskError) as mismatch:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="cancelled",
                        result="No longer planned.",
                        evidence=[],
                        run_id=OTHER_RUN_ID,
                    )

        self.assertEqual("archive_run_id_required", missing.exception.code)
        self.assertEqual("active_agent_run_mismatch", mismatch.exception.code)

    def test_unclaimed_terminal_archive_rejects_stale_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled", []),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    archive_task(
                        repository,
                        ACTOR,
                        PARENT_ID,
                        outcome="cancelled",
                        result="No longer planned.",
                        evidence=[],
                        run_id=RUN_ID,
                    )

        self.assertEqual("archive_run_id_unexpected", raised.exception.code)

    def test_terminal_archive_matching_run_clears_every_active_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("review", ["alice", "bob"]),
            ):
                start_agent(repository, ACTOR, task_id=PARENT_ID, run_id=RUN_ID)
                start_agent(
                    repository,
                    OTHER_ACTOR,
                    task_id=PARENT_ID,
                    run_id=OTHER_RUN_ID,
                )

            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled", []),
            ):
                archived = archive_task(
                    repository,
                    ACTOR,
                    PARENT_ID,
                    outcome="cancelled",
                    result="No longer planned.",
                    evidence=[],
                    run_id=RUN_ID,
                )

        self.assertEqual([], archived["task"]["active_agents"])
        self.assertEqual(
            [
                {"login": "alice", "run_id": RUN_ID},
                {"login": "bob", "run_id": OTHER_RUN_ID},
            ],
            archived["task"]["completion"]["participants"],
        )

    def test_unclaimed_terminal_archive_requires_task_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, PARENT_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled", ["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    archive_task(
                        repository,
                        OTHER_ACTOR,
                        PARENT_ID,
                        outcome="cancelled",
                        result="No longer planned.",
                        evidence=[],
                        run_id=None,
                    )

        self.assertEqual("archive_creator_required", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
