from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask.cli import _reconcile, _text
from wuditask.util import atomic_write_json
from wuditask.workflow import archive_task, claim_task

from tests.helpers import ACTOR, add_task, make_repository

TASK_ID = "WDT-20260711T120000Z-A1B2C3"


def delivery(state: str, *, assignees: list[str] | None = None) -> dict[str, object]:
    return {
        "status": "fresh",
        "delivery_state": state,
        "assignees": assignees or [],
        "prs": [],
        "updated_at": "2026-07-16T10:00:00Z",
        "fetched_at": "2026-07-16T10:00:01Z",
        "error": None,
        "url": "https://github.com/acme/service/issues/42",
    }


class ReconcileTests(unittest.TestCase):
    def test_plain_text_renderer_preserves_reconcile_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, TASK_ID)
            rendered = _text(_reconcile(repository, TASK_ID))

        self.assertIn(TASK_ID, rendered)
        self.assertIn("DELIVERY", rendered)
        self.assertIn("consistent", rendered)

    def test_archived_done_detects_reopened_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", assignees=["alice"]),
            ):
                claim_task(repository, ACTOR, task_id=TASK_ID)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("verification_needed"),
            ):
                archive_task(
                    repository,
                    ACTOR,
                    TASK_ID,
                    outcome="done",
                    result="Verified.",
                    evidence={"AC-1": "Regression passed."},
                )
            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery("assigned", assignees=["alice"]),
            ):
                report = _reconcile(repository, TASK_ID)["tasks"][0]

        self.assertEqual(
            ["archived_outcome_delivery_mismatch"],
            [item["code"] for item in report["observations"]],
        )

    def test_archived_cancelled_detects_completed_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("cancelled"),
            ):
                archive_task(
                    repository,
                    ACTOR,
                    TASK_ID,
                    outcome="cancelled",
                    result="Requirement withdrawn.",
                    evidence={},
                )
            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery("verification_needed"),
            ):
                report = _reconcile(repository, TASK_ID)["tasks"][0]

        self.assertFalse(report["consistent"])
        self.assertEqual(
            "archived_outcome_delivery_mismatch",
            report["observations"][0]["code"],
        )

    def test_claimed_task_detects_an_additional_github_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)
            with patch(
                "wuditask.workflow.fetch_delivery",
                return_value=delivery("assigned", assignees=["alice"]),
            ):
                claim_task(repository, ACTOR, task_id=TASK_ID)
            with patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery("assigned", assignees=["alice", "bob"]),
            ):
                report = _reconcile(repository, TASK_ID)["tasks"][0]

        self.assertEqual(
            "claim_delivery_multiple_owners",
            report["observations"][0]["code"],
        )


if __name__ == "__main__":
    unittest.main()
