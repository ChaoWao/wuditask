from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask import execution_cli as cli
from wuditask.cli import _parser, run
from wuditask.errors import WudiTaskError
from wuditask.gitops import GitCoordinator
from tests import test_personal_coordination as fixtures


class ExecutionCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PersonalGitTests()
        self.fixture.setUp()
        self.base = self.fixture.base
        self.actor = {"actor_id": "12", "login": "alice"}
        self.task = {
            "task_id": "I_TASK",
            "url": "https://github.com/acme/service/issues/12",
        }
        self.root_patch = patch.object(
            cli, "personal_root", return_value=self.base / "personal"
        )
        self.actor_patch = patch.object(
            cli, "authenticated_actor", return_value=self.actor
        )
        self.task_patch = patch.object(cli, "resolve_task", return_value=self.task)
        for p in (self.root_patch, self.actor_patch, self.task_patch):
            p.start()
            self.addCleanup(p.stop)
        self.call("configure", "--remote", str(self.fixture.origin))
        self.session = self.base / "run.json"

    def tearDown(self):
        self.fixture.tearDown()

    def call(self, *arguments):
        args = _parser().parse_args(["coord", *map(str, arguments)])
        return run(args, Path(__file__).resolve().parents[1])

    def acquire(self):
        return self.call("acquire", self.task["url"], "--session", self.session)

    def test_lifecycle_no_add_assign_archive_and_durable_retry(self):
        first = self.acquire()
        retry = self.acquire()
        self.assertEqual(first["run_id"], retry["run_id"])
        self.assertFalse(retry["changed"])
        self.assertTrue(self.call("check", "--session", self.session)["run"])
        self.call("quiesce", "--session", self.session)
        proof = self.base / "stop.json"
        proof.write_text(json.dumps(fixtures.receipt(first["run"])))
        self.call("release", "--session", self.session, "--receipt", proof)
        self.assertFalse(
            self.call("release", "--session", self.session, "--receipt", proof)[
                "changed"
            ]
        )
        record = self.call("inspect", self.task["url"])
        self.assertIsNone(record["current"])
        self.assertEqual(
            fixtures.receipt(first["run"])["checkpoint"],
            record["runs"][first["run_id"]]["checkpoint"],
        )
        self.session = self.base / "next.json"
        self.assertNotEqual(first["run_id"], self.acquire()["run_id"])

    def test_receipt_loss_keeps_journal_and_retry_run_id(self):
        push = GitCoordinator._push

        def lose(coordinator, checkout):
            result = push(coordinator, checkout)
            self.assertEqual(0, result.returncode)
            return subprocess.CompletedProcess(result.args, 1, "", "connection lost")

        with (
            patch.object(GitCoordinator, "_push", lose),
            patch.object(GitCoordinator, "_remote_head_containing", return_value=None),
        ):
            with self.assertRaises(WudiTaskError):
                self.acquire()
        saved = json.loads(self.session.read_text())
        result = self.acquire()
        self.assertEqual(saved["run_id"], result["run_id"])
        self.assertFalse(result["changed"])

    def test_historical_acceptance_does_not_authorize_retired_claim(self):
        write = cli._write

        def retire_after_write(coord, actor, action, operation):
            result = write(coord, actor, action, operation)
            if action == "acquire":
                t = result["run"]
                write(
                    coord,
                    actor,
                    "recover",
                    lambda repo: repo.release(
                        t, fixtures.receipt(t, recovery=True), recovery=True
                    ),
                )
            return result

        with patch.object(cli, "_write", side_effect=retire_after_write):
            with self.assertRaises(WudiTaskError) as error:
                self.acquire()
        self.assertEqual("execution_not_active", error.exception.code)

    def test_another_environment_cannot_resume_and_account_cannot_change(self):
        self.acquire()
        config_path = self.base / "personal/config.json"
        config = json.loads(config_path.read_text())
        config["environment"] = "another-machine"
        config_path.write_text(json.dumps(config))
        with self.assertRaises(WudiTaskError):
            self.acquire()
        with patch.object(
            cli, "authenticated_actor", return_value={"actor_id": "13", "login": "bob"}
        ):
            with self.assertRaises(WudiTaskError):
                self.call("inspect", self.task["url"])
        with self.assertRaises(WudiTaskError):
            self.call("configure", "--remote", str(self.base / "other.git"))

    def test_local_session_lock_prevents_second_start_or_release(self):
        first = self.acquire()
        saved = json.loads(self.session.read_text())
        with cli.session_lock(saved):
            with self.assertRaises(WudiTaskError) as error:
                self.call(
                    "guard",
                    "--session",
                    self.session,
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                )
            self.assertEqual("local_session_busy", error.exception.code)
            with self.assertRaises(WudiTaskError):
                self.call("quiesce", "--session", self.session)
        self.assertEqual(first["run_id"], saved["run_id"])

    def test_guard_real_process_runs_once_and_does_not_release(self):
        result = self.acquire()
        marker = self.base / "host-started"
        command = (
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"
        )
        launched = self.call(
            "guard", "--session", self.session, "--", sys.executable, "-c", command
        )
        self.assertTrue(marker.exists())
        self.assertEqual(0, launched["returncode"])
        self.assertFalse(launched["released"])
        with self.assertRaises(WudiTaskError):
            self.call(
                "guard", "--session", self.session, "--", sys.executable, "-c", command
            )
        self.assertEqual(
            result["run_id"],
            self.call("check", "--session", self.session)["run"]["run_id"],
        )

    def test_host_failure_reports_failure_without_releasing(self):
        self.acquire()
        with self.assertRaises(WudiTaskError) as error:
            self.call(
                "guard",
                "--session",
                self.session,
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(7)",
            )
        self.assertEqual("host_command_failed", error.exception.code)
        self.assertEqual(7, error.exception.details["returncode"])
        self.assertIsNotNone(self.call("inspect", self.task["url"])["current"])

    def test_cannot_store_credentials_in_authority_url(self):
        with self.assertRaises(WudiTaskError) as error:
            self.call(
                "configure", "--remote", "https://user:password@github.com/a/b.git"
            )
        self.assertEqual("credential_in_remote", error.exception.code)

    def test_copied_journal_cannot_launch_again(self):
        self.acquire()
        copy = self.base / "copied.json"
        copy.write_bytes(self.session.read_bytes())
        with self.assertRaises(WudiTaskError):
            self.call("guard", "--session", copy, "--", sys.executable, "-c", "pass")

    def test_failed_launch_and_unknown_old_process_do_not_auto_retry(self):
        self.acquire()
        with self.assertRaises(WudiTaskError):
            self.call(
                "guard", "--session", self.session, "--", "/nonexistent/executable"
            )
        with self.assertRaises(WudiTaskError):
            self.call(
                "guard", "--session", self.session, "--", sys.executable, "-c", "pass"
            )
        self.assertIsNotNone(self.call("inspect", self.task["url"])["current"])

    def test_recover_without_old_session_file(self):
        first = self.acquire()
        self.session.unlink()
        proof = self.base / "recovery.json"
        proof.write_text(json.dumps(fixtures.receipt(first["run"], recovery=True)))
        self.call(
            "recover", self.task["url"], "--run-id", first["run_id"], "--receipt", proof
        )
        self.assertIsNone(self.call("inspect", self.task["url"])["current"])


class GitHubIdentityTests(unittest.TestCase):
    def test_alias_urls_and_transferred_repo_use_api_node_identity(self):
        with patch.object(
            cli,
            "github_api",
            return_value={
                "node_id": "I_SAME",
                "html_url": "https://github.com/new/repo/issues/9",
            },
        ):
            a = cli.resolve_task("https://github.com/OLD/Repo/issues/1#issuecomment-3")
            b = cli.resolve_task("https://github.com/new/repo/issues/9/")
        self.assertEqual(a, b)

    def test_associated_pr_requires_explicit_anchor(self):
        def api(endpoint, *args):
            if endpoint == "graphql":
                return {
                    "data": {
                        "node": {
                            "closingIssuesReferences": {
                                "nodes": [
                                    {
                                        "id": "I_TASK",
                                        "url": "https://github.com/a/b/issues/2",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            if "pulls" in endpoint:
                return {
                    "node_id": "PR_TASK",
                    "html_url": "https://github.com/a/b/pull/1",
                }
            return {"node_id": "I_TASK", "html_url": "https://github.com/a/b/issues/2"}

        with patch.object(cli, "github_api", side_effect=api):
            with self.assertRaises(WudiTaskError) as error:
                cli.resolve_task("https://github.com/a/b/pull/1")
            self.assertEqual("task_anchor_required", error.exception.code)
            result = cli.resolve_task(
                "https://github.com/a/b/pull/1", "https://github.com/a/b/issues/2"
            )
            self.assertEqual("I_TASK", result["task_id"])

    def test_issue_spelling_of_pr_resolves_same_pull_node(self):
        def api(endpoint, *args):
            if endpoint == "graphql":
                return {
                    "data": {
                        "node": {
                            "closingIssuesReferences": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            if "issues" in endpoint:
                return {"node_id": "I_ALIAS", "pull_request": {}}
            return {"node_id": "PR_TASK", "html_url": "https://github.com/a/b/pull/1"}

        with patch.object(cli, "github_api", side_effect=api):
            self.assertEqual(
                cli.resolve_task("https://github.com/a/b/issues/1"),
                cli.resolve_task("https://github.com/a/b/pull/1"),
            )

    def test_authenticated_user_uses_stable_id_not_display_login(self):
        with patch.object(
            cli, "github_api", return_value={"id": 12, "login": "renamed"}
        ):
            self.assertEqual(
                {"actor_id": "12", "login": "renamed"}, cli.authenticated_actor()
            )
        with patch.object(cli, "github_api", return_value={"login": "renamed"}):
            with self.assertRaises(WudiTaskError):
                cli.authenticated_actor()


if __name__ == "__main__":
    unittest.main()
