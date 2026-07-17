from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json

from tests.helpers import RUN_ID, add_task
from tests.test_cli import FakeGitHub

TASK_A = "WDT-20260711T120000Z-A1B2C3"
TASK_B = "WDT-20260711T120001Z-B2C3D4"


class CheckCliTests(unittest.TestCase):
    @staticmethod
    def run_check(
        hub: Path,
        github: FakeGitHub,
        task_id: str | None = None,
        *,
        as_json: bool = True,
        environment: dict[str, str] | None = None,
    ):
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        command = [
            sys.executable,
            str(root / "tools" / "wuditask.py"),
            "--hub",
            str(hub),
            "--local",
        ]
        if as_json:
            command.append("--json")
        command.append("check")
        if task_id:
            command.append(task_id)
        return subprocess.run(
            command,
            cwd=root,
            env=environment or github.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_check_one_combines_dependencies_owners_agents_pr_checks_and_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            add_task(repository, TASK_A, number=12)
            task = add_task(
                repository,
                TASK_B,
                number=13,
                dependencies=[TASK_A],
            )
            task["active_agents"] = [{"login": "alice", "run_id": RUN_ID}]
            atomic_write_json(repository.open_dir / f"{TASK_B}.json", task)
            github = FakeGitHub(hub)
            github.issue(12)
            github.issue(13, assignees=["bob"], prs=[88])
            github.pull_request(
                88,
                author="carol",
                checks=[
                    {"status": "COMPLETED", "conclusion": "SUCCESS"},
                    {"status": "IN_PROGRESS", "conclusion": ""},
                ],
            )
            before = (repository.open_dir / f"{TASK_B}.json").read_bytes()

            result = self.run_check(hub, github, TASK_B)

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(1, len(payload["tasks"]))
            report = payload["tasks"][0]
            self.assertEqual(TASK_B, report["id"])
            self.assertEqual("open", report["location"])
            self.assertEqual(["bob", "carol"], report["owners"])
            self.assertEqual(
                [{"login": "alice", "run_id": RUN_ID}],
                report["active_agents"],
            )
            self.assertFalse(report["dependency_check"]["ready"])
            self.assertEqual(TASK_A, report["dependency_check"]["blockers"][0]["id"])
            self.assertEqual(
                {"total": 2, "successful": 1, "pending": 1, "failed": 0},
                report["delivery"]["prs"][0]["checks"],
            )
            self.assertIn(
                "active_agent_not_owner",
                [item["code"] for item in report["observations"]],
            )
            self.assertEqual(before, (repository.open_dir / f"{TASK_B}.json").read_bytes())

    def test_check_all_refreshes_every_open_task_and_summarizes_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            add_task(repository, TASK_A, number=12)
            add_task(repository, TASK_B, number=13, dependencies=[TASK_A])
            github = FakeGitHub(hub)
            github.issue(12, assignees=["alice"])
            github.issue(13)

            result = self.run_check(hub, github)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([TASK_A, TASK_B], [item["id"] for item in payload["tasks"]])
        self.assertEqual(2, payload["summary"]["checked"])
        self.assertEqual(1, payload["summary"]["ready"])
        self.assertEqual(1, payload["summary"]["blocked"])
        self.assertEqual(0, payload["summary"]["in_progress"])

    def test_check_preserves_unavailable_as_unknown_not_unowned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            add_task(repository, TASK_A, number=12)
            github = FakeGitHub(hub)
            environment = {**github.environment, "FAKE_GH_FAIL_VIEW": "1"}

            result = self.run_check(
                hub,
                github,
                TASK_A,
                environment=environment,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)["tasks"][0]
        self.assertEqual("unavailable", report["delivery"]["status"])
        self.assertIsNone(report["owners"])
        self.assertIn(
            "github_delivery_unavailable",
            [item["code"] for item in report["observations"]],
        )

    def test_check_archived_task_reports_reopened_delivery_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            task = add_task(repository, TASK_A, number=12)
            task["completion"] = {
                "outcome": "done",
                "completed_at": "2026-07-16T12:00:00Z",
                "completed_by": "alice",
                "result": "Verified and merged.",
                "evidence": ["python3 -m unittest: 12 passed"],
                "participants": [{"login": "alice", "run_id": RUN_ID}],
            }
            repository.archive(task)
            github = FakeGitHub(hub)
            github.issue(12, assignees=["alice"])

            result = self.run_check(hub, github, TASK_A)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)["tasks"][0]
        self.assertEqual("archive", report["location"])
        self.assertIn(
            "archived_outcome_delivery_mismatch",
            [item["code"] for item in report["observations"]],
        )

    def test_plain_check_output_names_owners_and_active_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            task = add_task(repository, TASK_A, number=12)
            task["active_agents"] = [{"login": "alice", "run_id": RUN_ID}]
            atomic_write_json(repository.open_dir / f"{TASK_A}.json", task)
            github = FakeGitHub(hub)
            github.issue(12, assignees=["alice", "bob"])

            result = self.run_check(hub, github, TASK_A, as_json=False)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(TASK_A, result.stdout)
        self.assertIn("OWNERS", result.stdout.upper())
        self.assertIn("alice", result.stdout)
        self.assertIn("ACTIVE", result.stdout.upper())


if __name__ == "__main__":
    unittest.main()
