from __future__ import annotations

import unittest
from unittest.mock import patch

from wuditask.cli import _finalize_claim_delivery
from wuditask.errors import WudiTaskError

from tests.helpers import ACTOR


class FakeCoordinator:
    distributed = True

    def __init__(self) -> None:
        self.writes = 0

    def write(self, operation, actor, message):  # type: ignore[no-untyped-def]
        self.writes += 1
        return {
            "task_id": "WDT-20260711T120000Z-A1B2C3",
            "confirmed": True,
            "changed": True,
            "sync": {"confirmed": True},
        }


def delivery(
    state: str,
    *,
    status: str = "fresh",
    assignees: list[str] | None = None,
    author: str | None = None,
) -> dict[str, object]:
    prs = []
    if author:
        prs.append(
            {
                "state": "OPEN",
                "merged_at": None,
                "author": author,
                "assignees": [],
            }
        )
    return {
        "status": status,
        "delivery_state": state,
        "assignees": assignees or [],
        "prs": prs,
        "updated_at": None,
        "fetched_at": "2026-07-16T10:00:00Z",
        "error": "unavailable" if status != "fresh" else None,
        "url": "https://github.com/acme/service/issues/42",
    }


def claimed(
    *,
    source_kind: str = "github_issue",
    changed: bool = True,
) -> dict[str, object]:
    initial = delivery("assigned", assignees=["alice"])
    return {
        "task_id": "WDT-20260711T120000Z-A1B2C3",
        "task": {
            "source": {
                "kind": source_kind,
                "repo": "acme/service",
                "number": 42,
            },
            "claim": {"token": "lease-token"},
        },
        "changed": changed,
        "lease_acquired": changed,
        "delivery": initial,
        "delivery_eligibility": {
            "eligible": True,
            "decision": "adopt",
            "owners": ["alice"],
        },
    }


class CliDeliverySyncTests(unittest.TestCase):
    def test_post_push_adopt_race_releases_new_lease(self) -> None:
        coordinator = FakeCoordinator()
        with patch(
            "wuditask.cli.fetch_delivery",
            return_value=delivery("assigned", assignees=["bob"]),
        ):
            with self.assertRaises(WudiTaskError) as raised:
                _finalize_claim_delivery(coordinator, ACTOR, claimed())

        self.assertEqual("github_claim_reconciliation_failed", raised.exception.code)
        self.assertEqual(1, coordinator.writes)

    def test_post_push_api_failure_releases_new_lease(self) -> None:
        coordinator = FakeCoordinator()
        with patch(
            "wuditask.cli.fetch_delivery",
            return_value=delivery("unavailable", status="unavailable"),
        ):
            with self.assertRaises(WudiTaskError) as raised:
                _finalize_claim_delivery(coordinator, ACTOR, claimed())

        self.assertEqual("github_claim_reconciliation_failed", raised.exception.code)
        self.assertEqual(1, coordinator.writes)

    def test_existing_lease_is_retained_when_post_check_changes_owner(self) -> None:
        coordinator = FakeCoordinator()
        with patch(
            "wuditask.cli.fetch_delivery",
            return_value=delivery("assigned", assignees=["bob"]),
        ):
            with self.assertRaises(WudiTaskError) as raised:
                _finalize_claim_delivery(
                    coordinator,
                    ACTOR,
                    claimed(changed=False),
                )

        self.assertEqual("github_claim_reconciliation_required", raised.exception.code)
        self.assertEqual(0, coordinator.writes)

    def test_pull_request_source_is_rechecked_after_push(self) -> None:
        coordinator = FakeCoordinator()
        with (
            patch(
                "wuditask.cli.fetch_delivery",
                return_value=delivery("review", author="alice"),
            ),
            patch("wuditask.cli.update_issue_assignee") as assignment,
        ):
            result = _finalize_claim_delivery(
                coordinator,
                ACTOR,
                claimed(source_kind="github_pull_request"),
            )

        self.assertTrue(result["work_authorized"])
        assignment.assert_not_called()
        self.assertEqual(0, coordinator.writes)


if __name__ == "__main__":
    unittest.main()
