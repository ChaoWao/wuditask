from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from wuditask.errors import WudiTaskError
from wuditask.gitops import GitCoordinator
from wuditask.repository import TaskRepository
from wuditask.workflow import claim_task, create_task

from tests.helpers import (
    ACTOR,
    OTHER_ACTOR,
    add_task,
    git,
    make_hub_origin,
    make_repository,
    spec,
)

FIRST_ID = "WDT-20260711T120000Z-111111"
SECOND_ID = "WDT-20260711T120001Z-222222"


class GitConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.origin = self.base / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(self.origin)],
            cwd=self.base,
            check=True,
            capture_output=True,
            text=True,
        )
        seed = self.base / "seed"
        seed.mkdir()
        git(["init", "-b", "main"], seed)
        git(["config", "user.name", "seed"], seed)
        git(["config", "user.email", "seed@example.invalid"], seed)
        repository = make_repository(seed)
        add_task(repository, FIRST_ID, title="First task")
        add_task(repository, SECOND_ID, title="Second task")
        git(["add", "hub.json", "data"], seed)
        git(["commit", "-m", "seed tasks"], seed)
        git(["remote", "add", "origin", str(self.origin)], seed)
        git(["push", "-u", "origin", "main"], seed)
        self.client_a = self.base / "client-a"
        self.client_b = self.base / "client-b"
        git(["clone", str(self.origin), str(self.client_a)], self.base)
        git(["clone", str(self.origin), str(self.client_b)], self.base)
        self.cache_root = self.base / "cache"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _race(
        self,
        first_target: str,
        second_target: str,
    ) -> tuple[list[dict[str, Any]], list[Exception]]:
        barrier = threading.Barrier(2)

        def before_push(attempt: int, _checkout: Path) -> None:
            if attempt == 1:
                barrier.wait(timeout=10)

        coordinators = (
            GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.cache_root,
                before_push=before_push,
                max_attempts=6,
            ),
            GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.cache_root,
                before_push=before_push,
                max_attempts=6,
            ),
        )
        calls = (
            (coordinators[0], ACTOR, first_target),
            (coordinators[1], OTHER_ACTOR, second_target),
        )
        results: list[dict[str, Any]] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def run(
            coordinator: GitCoordinator,
            actor: Any,
            target: str,
        ) -> None:
            try:
                result = coordinator.write(
                    lambda repository: claim_task(
                        repository,
                        actor,
                        task_id=target,
                    ),
                    actor,
                    lambda payload: f"wuditask: claim {payload['task_id']}",
                )
                with lock:
                    results.append(result)
            except Exception as error:
                with lock:
                    errors.append(error)

        threads = [threading.Thread(target=run, args=call) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(
                thread.is_alive(), "concurrent Git transaction did not finish"
            )
        self.assertEqual([], list((self.cache_root / "operations").iterdir()))
        cache_paths = list((self.cache_root / "hubs").glob("*.git"))
        self.assertEqual(1, len(cache_paths))
        identity_config = subprocess.run(
            ["git", "config", "--local", "--get-regexp", r"^user\."],
            cwd=cache_paths[0],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, identity_config.returncode)
        self.assertEqual("", identity_config.stdout)
        return results, errors

    def _remote_index(self) -> Any:
        checkout = self.base / "inspect"
        git(["clone", str(self.origin), str(checkout)], self.base)
        return TaskRepository(checkout).load_index()

    def test_different_tasks_both_succeed_after_retry(self) -> None:
        results, errors = self._race(FIRST_ID, SECOND_ID)
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertTrue(all(result["sync"]["confirmed"] for result in results))
        self.assertGreaterEqual(
            max(result["sync"]["attempts"] for result in results), 2
        )
        index = self._remote_index()
        self.assertEqual("alice", index.open[FIRST_ID].task["owner"]["login"])
        self.assertEqual("bob", index.open[SECOND_ID].task["owner"]["login"])
        authors = git(
            ["log", "-2", "--format=%an", "refs/heads/main"],
            self.origin,
        ).stdout.splitlines()
        self.assertCountEqual(["alice", "bob"], authors)

    def test_same_task_has_exactly_one_confirmed_owner(self) -> None:
        results, errors = self._race(FIRST_ID, FIRST_ID)
        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["sync"]["confirmed"])
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], WudiTaskError)
        self.assertEqual("claim_conflict", errors[0].code)
        index = self._remote_index()
        owner = index.open[FIRST_ID].task["owner"]["login"]
        self.assertIn(owner, {"alice", "bob"})

    def test_accepted_push_with_lost_response_is_reconciled(self) -> None:
        class AmbiguousPushCoordinator(GitCoordinator):
            def _push(self, checkout: Path) -> subprocess.CompletedProcess[str]:
                accepted = super()._push(checkout)
                self.assert_success(accepted)
                return subprocess.CompletedProcess(
                    accepted.args,
                    1,
                    stdout=accepted.stdout,
                    stderr="simulated connection reset after server accepted the push",
                )

            @staticmethod
            def assert_success(process: subprocess.CompletedProcess[str]) -> None:
                if process.returncode != 0:
                    raise AssertionError(process.stderr)

        coordinator = AmbiguousPushCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )
        result = coordinator.write(
            lambda repository: claim_task(repository, ACTOR, task_id=FIRST_ID),
            ACTOR,
            lambda payload: f"wuditask: claim {payload['task_id']}",
        )
        self.assertTrue(result["sync"]["confirmed"])
        self.assertEqual("remote_reconciliation", result["sync"]["confirmation"])
        self.assertEqual(
            "alice",
            self._remote_index().open[FIRST_ID].task["owner"]["login"],
        )

    def test_hub_push_command_never_forces_remote_history(self) -> None:
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )
        commands: list[list[str]] = []

        def capture(
            command: list[str],
            *,
            cwd: Path,
            allowed: set[int] | None = {0},
        ) -> subprocess.CompletedProcess[str]:
            self.assertEqual(self.client_a, cwd)
            self.assertIsNone(allowed)
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        coordinator._run = capture  # type: ignore[method-assign]
        coordinator._push(self.client_a)

        self.assertEqual(
            [
                [
                    "git",
                    "-c",
                    "remote.origin.mirror=false",
                    "-c",
                    "push.followTags=false",
                    "push",
                    "--no-force",
                    "--no-force-with-lease",
                    "--no-mirror",
                    "--no-follow-tags",
                    "--",
                    str(self.origin),
                    "HEAD:refs/heads/main",
                ]
            ],
            commands,
        )

    def test_hub_push_disables_inherited_mirror_mode(self) -> None:
        git(["config", "remote.origin.mirror", "true"], self.client_a)
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        push = coordinator._push(self.client_a)

        self.assertEqual(0, push.returncode, push.stderr)

    def test_hub_push_does_not_follow_tags_from_git_config(self) -> None:
        git(["config", "push.followTags", "true"], self.client_a)
        git(["tag", "-a", "must-not-push", "-m", "local-only tag"], self.client_a)
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        push = coordinator._push(self.client_a)
        remote_tag = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/tags/must-not-push"],
            cwd=self.origin,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, push.returncode, push.stderr)
        self.assertNotEqual(0, remote_tag.returncode)

    def test_hub_push_ignores_origin_pushurl_override(self) -> None:
        decoy = self.base / "decoy.git"
        git(["init", "--bare", "--initial-branch=main", str(decoy)], self.base)
        git(["config", "remote.origin.pushurl", str(decoy)], self.client_a)
        git(["config", "user.name", "alice"], self.client_a)
        git(["config", "user.email", "alice@example.invalid"], self.client_a)
        (self.client_a / "push-target-marker").write_text("configured hub\n")
        git(["add", "push-target-marker"], self.client_a)
        git(["commit", "-m", "advance configured hub"], self.client_a)
        expected = git(["rev-parse", "HEAD"], self.client_a).stdout.strip()
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        push = coordinator._push(self.client_a)
        actual = git(["rev-parse", "refs/heads/main"], self.origin).stdout.strip()
        decoy_branch = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/main"],
            cwd=decoy,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, push.returncode, push.stderr)
        self.assertEqual(expected, actual)
        self.assertNotEqual(0, decoy_branch.returncode)

    def test_remote_write_advances_only_configured_hub_branch(self) -> None:
        hub = make_hub_origin(self.base, name="separate-hub", branch="queue")
        tool = self.base / "tool"
        tool.mkdir()
        git(["init", "-b", "main"], tool)
        git(["config", "user.name", "tool"], tool)
        git(["config", "user.email", "tool@example.invalid"], tool)
        (tool / "VERSION").write_text("1\n", encoding="utf-8")
        git(["add", "VERSION"], tool)
        git(["commit", "-m", "tool version"], tool)
        tool_head = git(["rev-parse", "HEAD"], tool).stdout.strip()
        hub_head = git(["rev-parse", "refs/heads/queue"], hub).stdout.strip()

        coordinator = GitCoordinator(
            remote=str(hub),
            branch="queue",
            cache_root=self.cache_root,
        )
        result = coordinator.write(
            lambda repository: create_task(
                repository,
                spec("Separate Hub task"),
                ACTOR,
                task_id="WDT-20260711T120002Z-333333",
                now="2026-07-11T12:00:02Z",
            ),
            ACTOR,
            lambda payload: f"wuditask: add {payload['task_id']}",
        )

        new_hub_head = git(["rev-parse", "refs/heads/queue"], hub).stdout.strip()
        changed_paths = git(
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                new_hub_head,
            ],
            hub,
        ).stdout.splitlines()
        self.assertTrue(result["sync"]["confirmed"])
        self.assertNotEqual(hub_head, new_hub_head)
        self.assertEqual(tool_head, git(["rev-parse", "HEAD"], tool).stdout.strip())
        self.assertEqual([], git(["status", "--porcelain"], tool).stdout.splitlines())
        self.assertEqual(
            ["data/open/WDT-20260711T120002Z-333333.json"],
            changed_paths,
        )


if __name__ == "__main__":
    unittest.main()
