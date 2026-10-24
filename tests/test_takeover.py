from __future__ import annotations

import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask import execution_cli as cli
from wuditask.cli import _parser, run
from wuditask.errors import WudiTaskError
from wuditask.gitops import GitCoordinator
from tests import test_personal_coordination as fixtures
from tests.test_install import make_tool


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PersonalGitTests()
        self.fixture.setUp()
        self.base = self.fixture.base
        self.coord = self.fixture.coord
        self.actor = {"actor_id": "12", "login": "alice"}

    def tearDown(self):
        self.fixture.tearDown()

    def change(self, operation, coord=None):
        return (coord or self.coord).write(
            operation, fixtures.ACTOR, lambda _: "test execution"
        )

    def test_active_old_writer_not_required_to_stop(self):
        old, new = fixtures.ticket(), fixtures.ticket(environment="machine-b")
        self.change(lambda repo: repo.acquire(old))
        self.change(
            lambda repo: repo.checkpoint(old, "patch staged; tests still needed")
        )
        result = self.change(
            lambda repo: repo.takeover(new, old["run_id"], "User: continue here")
        )
        self.assertEqual(
            "patch staged; tests still needed", result["run"]["checkpoint"]
        )
        with self.coord.snapshot() as repo:
            repo.assert_current(new)
            with self.assertRaises(WudiTaskError):
                repo.assert_current(old)
            with self.assertRaises(WudiTaskError):
                repo.checkpoint(old, "stale update")
            with self.assertRaises(WudiTaskError):
                repo.finish(old, "stale finish", "completed")
            old_record = repo.inspect(old["actor_id"], old["task_id"])["runs"][
                old["run_id"]
            ]
            self.assertFalse(old_record["stop_verified"])
            self.assertEqual("superseded", old_record["disposition"])

    def test_two_takeovers_of_observed_run_only_one_wins(self):
        old = fixtures.ticket()
        self.change(lambda repo: repo.acquire(old))
        barrier = threading.Barrier(2)
        results, errors = [], []

        def before(attempt, checkout):
            if attempt == 1:
                barrier.wait(timeout=10)

        def attempt():
            ticket = fixtures.ticket(environment="replacement")
            try:
                coord = self.fixture.coordinator(before_push=before)
                results.append(
                    self.change(
                        lambda repo: repo.takeover(
                            ticket, old["run_id"], "Explicit request"
                        ),
                        coord,
                    )
                )
            except WudiTaskError as error:
                errors.append(error.code)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(results))
        self.assertEqual(["takeover_conflict"], errors)

    def test_takeover_retries_and_stale_retry_never_reclaims(self):
        old, new, last = fixtures.ticket(), fixtures.ticket(), fixtures.ticket()
        self.change(lambda repo: repo.acquire(old))
        self.change(lambda repo: repo.takeover(new, old["run_id"], "request"))
        self.assertFalse(
            self.change(lambda repo: repo.takeover(new, old["run_id"], "request"))[
                "changed"
            ]
        )
        with self.assertRaises(WudiTaskError):
            self.change(lambda repo: repo.takeover(new, old["run_id"], "changed"))
        self.change(lambda repo: repo.takeover(last, new["run_id"], "next request"))
        with self.assertRaises(WudiTaskError):
            self.change(lambda repo: repo.takeover(new, old["run_id"], "request"))

    def test_takeover_never_touches_other_user(self):
        old = fixtures.ticket(actor="13")
        self.change(lambda repo: repo.acquire(old))
        new = fixtures.ticket()
        with self.assertRaises(WudiTaskError):
            self.change(
                lambda repo: repo.takeover(new, old["run_id"], "my account only")
            )
        self.change(lambda repo: repo.acquire(new))
        with self.coord.snapshot() as repo:
            repo.assert_current(old)
            repo.assert_current(new)

    def test_finish_is_cooperative_idempotent_and_does_not_clear_successor(self):
        old, new = fixtures.ticket(), fixtures.ticket()
        self.change(lambda repo: repo.acquire(old))
        self.change(lambda repo: repo.finish(old, "continue tests tomorrow", "paused"))
        self.change(lambda repo: repo.acquire(new))
        replay = self.change(
            lambda repo: repo.finish(old, "continue tests tomorrow", "paused")
        )
        self.assertFalse(replay["changed"])
        self.assertFalse(replay["run"]["stop_verified"])
        with self.assertRaises(WudiTaskError):
            self.change(lambda repo: repo.finish(old, "rewrite", "completed"))
        with self.coord.snapshot() as repo:
            repo.assert_current(new)

    def test_setup_registers_skill_for_personal_storage(self):
        home = self.base / "home"
        tool = make_tool(
            self.base / "tool-fixture",
            content_source=Path(__file__).resolve().parents[1],
        )
        with (
            patch.object(Path, "home", return_value=home),
            patch.object(cli, "authenticated_actor", return_value=self.actor),
        ):
            args = _parser().parse_args(
                [
                    "coord",
                    "setup",
                    "--remote",
                    str(self.fixture.origin),
                    "--branch",
                    "main",
                ]
            )
            result = run(args, tool)
            self.assertEqual("$wuditask", result["skill"])
            self.assertEqual(
                "personal-execution", result["installation"]["hub_validation"]["mode"]
            )
            self.assertTrue((home / ".agents/skills/wuditask/SKILL.md").is_file())
            self.assertTrue((home / ".claude/skills/wuditask/SKILL.md").is_file())
            self.assertTrue((home / ".wuditask/personal/config.json").is_file())
            self.assertFalse(run(args, tool)["branch_created"])


class TakeoverCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PersonalGitTests()
        self.fixture.setUp()
        self.base = self.fixture.base
        self.actor = {"actor_id": "12", "login": "alice"}
        self.task = {
            "task_id": "I_TASK",
            "url": "https://github.com/acme/service/issues/12",
        }
        for patcher in (
            patch.object(cli, "personal_root", return_value=self.base / "personal"),
            patch.object(cli, "authenticated_actor", return_value=self.actor),
            patch.object(cli, "resolve_task", return_value=self.task),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.call("configure", "--remote", str(self.fixture.origin))
        self.old = self.base / "old.json"
        self.new = self.base / "new.json"
        self.old_run = self.call("acquire", self.task["url"], "--session", self.old)[
            "run_id"
        ]

    def tearDown(self):
        self.fixture.tearDown()

    def call(self, *args):
        return run(
            _parser().parse_args(["coord", *map(str, args)]),
            Path(__file__).resolve().parents[1],
        )

    def take(self):
        return self.call(
            "takeover",
            self.task["url"],
            "--session",
            self.new,
            "--expected-run-id",
            self.old_run,
            "--reason",
            "User asked: continue here",
        )

    def test_normal_commands_need_no_stop_json(self):
        self.call("checkpoint", "--session", self.old, "--summary", "tests pending")
        taken = self.take()
        self.assertEqual("tests pending", taken["run"]["checkpoint"])
        with self.assertRaises(WudiTaskError):
            self.call("check", "--session", self.old)
        self.call(
            "finish",
            "--session",
            self.new,
            "--outcome",
            "completed",
            "--summary",
            "PR ready",
        )
        self.assertIsNone(self.call("inspect", self.task["url"])["current"])

    def test_takeover_response_loss_uses_same_journal(self):
        push = GitCoordinator._push

        def lose(coord, checkout):
            result = push(coord, checkout)
            self.assertEqual(0, result.returncode)
            return subprocess.CompletedProcess(result.args, 1, "", "lost response")

        with (
            patch.object(GitCoordinator, "_push", lose),
            patch.object(GitCoordinator, "_remote_head_containing", return_value=None),
        ):
            with self.assertRaises(WudiTaskError):
                self.take()
        saved = json.loads(self.new.read_text())
        result = self.take()
        self.assertEqual(saved["run_id"], result["run_id"])
        self.assertFalse(result["changed"])
        with self.assertRaises(WudiTaskError):
            self.call(
                "takeover",
                self.task["url"],
                "--session",
                self.new,
                "--expected-run-id",
                self.old_run,
                "--reason",
                "different instruction",
            )


if __name__ == "__main__":
    unittest.main()
