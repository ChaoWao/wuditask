from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.model import validate_task
from wuditask.workflow import create_task

from tests.helpers import ACTOR, RUN_ID, add_task, make_repository, spec

TASK_ID = "WDT-20260711T120000Z-A1B2C3"


class ModelTests(unittest.TestCase):
    def test_created_task_has_only_schema_three_coordination_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)

        self.assertEqual([], validate_task(task, archived=False))
        self.assertEqual(
            {
                "schema_version",
                "id",
                "repo",
                "source",
                "created_by",
                "priority",
                "created_at",
                "dependencies",
                "active_agents",
            },
            set(task),
        )
        self.assertEqual(3, task["schema_version"])
        self.assertEqual("alice", task["created_by"])
        self.assertEqual([], task["active_agents"])

    def test_add_requires_only_repo_and_github_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            with self.assertRaises(WudiTaskError) as raised:
                create_task(repository, {}, ACTOR, task_id=TASK_ID)

        self.assertEqual("insufficient_task_spec", raised.exception.code)
        self.assertEqual(["repo", "source"], raised.exception.details["missing"])

    def test_text_source_is_rejected(self) -> None:
        value = spec()
        value["source"] = {"kind": "text", "reason": "not canonical"}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(Exception) as raised:
                create_task(
                    make_repository(Path(temporary)),
                    value,
                    ACTOR,
                    task_id=TASK_ID,
                    now="2026-07-11T12:00:00Z",
                )
        self.assertTrue(
            any(issue["path"] == "$.source.kind" for issue in raised.exception.details["issues"])
        )

    def test_active_agents_require_unique_case_insensitive_login_and_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)
        task["active_agents"] = [
            {"login": "Alice", "run_id": RUN_ID},
            {"login": "alice", "run_id": "not-a-run"},
        ]
        issues = validate_task(task, archived=False)
        self.assertIn(
            {"path": "$.active_agents[1].login", "message": "must be unique ignoring case"},
            issues,
        )
        self.assertIn(
            {"path": "$.active_agents[1].run_id", "message": "must match WDX- followed by 24 hexadecimal characters"},
            issues,
        )

    def test_done_completion_requires_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)
        task["completion"] = {
            "outcome": "done",
            "completed_at": "2026-07-11T13:00:00Z",
            "completed_by": "alice",
            "result": "Done.",
            "evidence": [],
            "participants": [{"login": "alice", "run_id": RUN_ID}],
        }
        issues = validate_task(task, archived=True)
        self.assertIn(
            {"path": "$.completion.evidence", "message": "must be non-empty when outcome is done"},
            issues,
        )

    def test_archived_task_has_no_active_agents_and_completer_is_a_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)
        task["active_agents"] = [{"login": "alice", "run_id": RUN_ID}]
        task["completion"] = {
            "outcome": "failed",
            "completed_at": "2026-07-11T13:00:00Z",
            "completed_by": "bob",
            "result": "Failed.",
            "evidence": [],
            "participants": [{"login": "alice", "run_id": RUN_ID}],
        }
        issues = validate_task(task, archived=True)
        self.assertIn(
            {"path": "$.active_agents", "message": "must be empty in an archived task"},
            issues,
        )
        self.assertIn(
            {"path": "$.completion.completed_by", "message": "must identify a participant"},
            issues,
        )

    def test_unclaimed_terminal_completion_may_be_recorded_by_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)
        task["completion"] = {
            "outcome": "cancelled",
            "completed_at": "2026-07-11T13:00:00Z",
            "completed_by": "alice",
            "result": "No longer planned.",
            "evidence": [],
            "participants": [],
        }
        self.assertEqual([], validate_task(task, archived=True))

        task["completion"]["completed_by"] = "bob"
        self.assertIn(
            {
                "path": "$.completion.completed_by",
                "message": "must identify a participant or the task creator when participants are empty",
            },
            validate_task(task, archived=True),
        )

    def test_unclaimed_done_completion_may_be_recorded_by_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = add_task(make_repository(Path(temporary)), TASK_ID)
        task["completion"] = {
            "outcome": "done",
            "completed_at": "2026-07-11T13:00:00Z",
            "completed_by": "alice",
            "result": "Delivered outside an active WudiTask run.",
            "evidence": ["Merged pull request and checks passed"],
            "participants": [],
        }
        self.assertEqual([], validate_task(task, archived=True))

        task["completion"]["completed_by"] = "bob"
        self.assertIn(
            {
                "path": "$.completion.completed_by",
                "message": "must identify a participant or the task creator when participants are empty",
            },
            validate_task(task, archived=True),
        )

    def test_github_fallback_requires_external_repo_and_reason(self) -> None:
        value = spec()
        value["source"] = {
            "kind": "github_issue_fallback",
            "repo": "acme/hub",
            "number": 42,
            "fallback_reason": "Execution repository has Issues disabled.",
        }
        with tempfile.TemporaryDirectory() as temporary:
            task = create_task(
                make_repository(Path(temporary)), value, ACTOR, task_id=TASK_ID
            )["task"]
        self.assertEqual("github_issue_fallback", task["source"]["kind"])


if __name__ == "__main__":
    unittest.main()
