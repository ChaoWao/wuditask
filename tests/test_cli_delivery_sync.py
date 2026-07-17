from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from wuditask.cli import _execute, _finalize_agent_delivery
from wuditask.errors import WudiTaskError
from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json

from tests.helpers import ACTOR, OTHER_RUN_ID, RUN_ID, add_task, make_repository

TASK_ID = "WDT-20260711T120000Z-A1B2C3"


class FakeCoordinator:
    distributed = True

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository
        self.writes = 0

    def write(self, operation, actor, message):  # type: ignore[no-untyped-def]
        self.writes += 1
        result = operation(self.repository)
        result["sync"] = {
            "mode": "remote",
            "confirmed": True,
            "attempts": 1,
            "commit": "deadbeef",
        }
        return result


class HubConflictCoordinator:
    distributed = True

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository
        self.writes = 0

    @contextmanager
    def snapshot(self):  # type: ignore[no-untyped-def]
        yield self.repository

    def write(self, operation, actor, message):  # type: ignore[no-untyped-def]
        self.writes += 1
        raise WudiTaskError(
            "active_agent_conflict",
            "A concurrent Hub update started another run.",
            exit_code=3,
        )


def delivery(
    *,
    status: str = "fresh",
    state: str = "assigned",
    owners: list[str] | None = None,
    source_kind: str = "github_issue",
) -> dict[str, Any]:
    prs: list[dict[str, Any]] = []
    if source_kind == "github_pull_request":
        prs.append(
            {
                "repo": "acme/service",
                "number": 42,
                "author": (owners or [None])[0],
                "assignees": [],
                "state": "OPEN",
                "merged_at": None,
                "review_decision": "REVIEW_REQUIRED",
                "merge_state_status": "BLOCKED",
                "checks": {"total": 1, "successful": 0, "pending": 1, "failed": 0},
            }
        )
    return {
        "status": status,
        "delivery_state": state if status == "fresh" else "unavailable",
        "title": "Canonical task",
        "body": "Goal and acceptance.",
        "owners": owners or [],
        "assignees": owners or [],
        "prs": prs,
        "updated_at": "2026-07-16T10:00:00Z",
        "fetched_at": "2026-07-16T10:00:01Z",
        "error": None if status == "fresh" else "network unavailable",
        "url": (
            "https://github.com/acme/service/pull/42"
            if source_kind == "github_pull_request"
            else "https://github.com/acme/service/issues/42"
        ),
    }


def started(repository: TaskRepository, *, source_kind: str = "github_issue") -> dict[str, Any]:
    task = add_task(repository, TASK_ID, number=42)
    task["source"]["kind"] = source_kind
    task["active_agents"] = [{"login": "alice", "run_id": RUN_ID}]
    atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)
    return {
        "task_id": TASK_ID,
        "task": task,
        "run_id": RUN_ID,
        "confirmed": True,
        "changed": True,
        "agent_started": True,
        "sync": {"mode": "remote", "confirmed": True, "attempts": 1},
    }


class CliDeliverySyncTests(unittest.TestCase):
    def test_post_push_owner_recheck_authorizes_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository)
            refreshed = delivery(owners=["alice"])

            with patch("wuditask.cli.fetch_delivery", return_value=refreshed):
                finalized = _finalize_agent_delivery(coordinator, ACTOR, result)

        self.assertTrue(finalized["work_authorized"])
        self.assertEqual(refreshed, finalized["delivery"])
        self.assertEqual(0, coordinator.writes)

    def test_post_push_owner_race_releases_only_started_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository)

            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery(owners=["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    _finalize_agent_delivery(coordinator, ACTOR, result)

            active = repository.load_index().open[TASK_ID].task["active_agents"]

        self.assertEqual("execution_reconciliation_failed", raised.exception.code)
        self.assertEqual(1, coordinator.writes)
        self.assertEqual([], active)

    def test_post_push_delivery_failure_compensates_started_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository)

            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery(status="unavailable"),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    _finalize_agent_delivery(coordinator, ACTOR, result)

            active = repository.load_index().open[TASK_ID].task["active_agents"]

        self.assertEqual("execution_reconciliation_failed", raised.exception.code)
        self.assertEqual(1, coordinator.writes)
        self.assertEqual([], active)

    def test_stale_compensation_cannot_release_a_newer_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository)
            task = repository.load_index().open[TASK_ID].task
            task["active_agents"] = [{"login": "alice", "run_id": OTHER_RUN_ID}]
            atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)

            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery(owners=["bob"]),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    _finalize_agent_delivery(coordinator, ACTOR, result)

            active = repository.load_index().open[TASK_ID].task["active_agents"]

        self.assertEqual("execution_reconciliation_required", raised.exception.code)
        self.assertEqual(1, coordinator.writes)
        self.assertEqual([{"login": "alice", "run_id": OTHER_RUN_ID}], active)
        self.assertIn("release_error", raised.exception.details)

    def test_hub_start_conflict_does_not_rollback_confirmed_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID, number=42)
            coordinator = HubConflictCoordinator(repository)
            selected = {
                "task": task,
                "delivery": delivery(owners=[]),
                "dependency_check": {"ready": True},
                "needs_assignment": True,
            }
            unowned = delivery(owners=[])
            assigned = delivery(owners=["alice"])

            with (
                patch("wuditask.cli._select_execute_task", return_value=selected),
                patch(
                    "wuditask.cli.fetch_delivery",
                    side_effect=[unowned, assigned],
                ),
                patch(
                    "wuditask.cli.update_source_assignee",
                    return_value={"status": "updated", "changed": True, "error": None},
                ) as mutation,
                patch("wuditask.cli.new_run_id", return_value=RUN_ID),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    _execute(
                        coordinator,  # type: ignore[arg-type]
                        ACTOR,
                        task_id=TASK_ID,
                        repo="acme/service",
                    )

        self.assertEqual("active_agent_conflict", raised.exception.code)
        self.assertEqual(1, coordinator.writes)
        mutation.assert_called_once_with(task["source"], "alice", add=True)

    def test_pull_request_author_is_rechecked_without_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository, source_kind="github_pull_request")

            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery(
                    owners=["alice"],
                    state="review",
                    source_kind="github_pull_request",
                ),
            ):
                finalized = _finalize_agent_delivery(coordinator, ACTOR, result)

        self.assertTrue(finalized["work_authorized"])
        self.assertEqual(0, coordinator.writes)

    def test_terminal_delivery_never_authorizes_started_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            coordinator = FakeCoordinator(repository)
            result = started(repository)

            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery(owners=["alice"], state="verification_needed"),
            ):
                with self.assertRaises(WudiTaskError) as raised:
                    _finalize_agent_delivery(coordinator, ACTOR, result)
            active = repository.load_index().open[TASK_ID].task["active_agents"]

        self.assertEqual("execution_reconciliation_failed", raised.exception.code)
        self.assertEqual([], active)


if __name__ == "__main__":
    unittest.main()
