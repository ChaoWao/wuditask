from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.model import validate_task
from wuditask.workflow import create_task

from tests.helpers import ACTOR, add_task, make_repository, spec

TASK_ID = "WDT-20260711T120000Z-A1B2C3"


class ModelTests(unittest.TestCase):
    def test_created_task_matches_schema_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)

        self.assertEqual([], validate_task(task, archived=False))
        self.assertIsNone(task["claim"])
        self.assertNotIn("owner", task)
        self.assertEqual("text", task["source"]["kind"])
        self.assertNotIn("status", task)
        self.assertEqual({"login": "alice", "github_id": 1001}, task["created_by"])

    def test_add_reports_questions_for_insufficient_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            with self.assertRaises(WudiTaskError) as raised:
                create_task(
                    repository,
                    {"title": "Incomplete"},
                    ACTOR,
                    task_id=TASK_ID,
                    now="2026-07-11T12:00:00Z",
                )

        self.assertEqual("insufficient_task_spec", raised.exception.code)
        self.assertEqual(
            ["repo", "goal", "source", "acceptance_criteria"],
            raised.exception.details["missing"],
        )
        self.assertEqual(4, len(raised.exception.details["questions"]))

    def test_github_source_outside_execution_repo_requires_reason(self) -> None:
        value = spec()
        value["source"] = {
            "kind": "github_issue_fallback",
            "repo": "acme/wuditask-hub",
            "number": 42,
        }
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            with self.assertRaises(Exception) as raised:
                create_task(
                    repository,
                    value,
                    ACTOR,
                    task_id=TASK_ID,
                    now="2026-07-11T12:00:00Z",
                )
        self.assertTrue(
            any(
                issue["path"] == "$.source.fallback_reason"
                for issue in raised.exception.details["issues"]
            )
        )

    def test_github_source_outside_execution_repo_accepts_reason(self) -> None:
        value = spec()
        value["source"] = {
            "kind": "github_issue_fallback",
            "repo": "acme/wuditask-hub",
            "number": 42,
            "fallback_reason": "The execution repository has Issues disabled.",
        }
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = create_task(
                repository,
                value,
                ACTOR,
                task_id=TASK_ID,
                now="2026-07-11T12:00:00Z",
            )["task"]
        self.assertEqual("acme/wuditask-hub", task["source"]["repo"])

    def test_unknown_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
        task["agent_owner"] = "some-agent"
        issues = validate_task(task, archived=False)
        self.assertIn(
            {"path": "$.agent_owner", "message": "is not allowed"},
            issues,
        )

    def test_dependency_must_exist_when_added(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            value = spec(dependencies=["WDT-20260710T120000Z-FFFFFF"])
            with self.assertRaises(WudiTaskError) as raised:
                create_task(
                    repository,
                    value,
                    ACTOR,
                    task_id=TASK_ID,
                    now="2026-07-11T12:00:00Z",
                )
        self.assertEqual("missing_dependency", raised.exception.code)

    def test_add_with_same_explicit_id_and_spec_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            first = create_task(
                repository,
                spec(),
                ACTOR,
                task_id=TASK_ID,
                now="2026-07-11T12:00:00Z",
            )
            second = create_task(
                repository,
                spec(),
                ACTOR,
                task_id=TASK_ID,
                now="2026-07-11T12:00:00Z",
            )
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertTrue(second["already_added"])

    def test_incomplete_acceptance_returns_questions(self) -> None:
        value = spec()
        value["acceptance_criteria"] = [{"description": "", "verification": {}}]
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            with self.assertRaises(WudiTaskError) as raised:
                create_task(
                    repository,
                    value,
                    ACTOR,
                    task_id=TASK_ID,
                    now="2026-07-11T12:00:00Z",
                )
        self.assertEqual("insufficient_task_spec", raised.exception.code)
        self.assertIn(
            "acceptance_criteria[0].verification",
            raised.exception.details["missing"],
        )


if __name__ == "__main__":
    unittest.main()
