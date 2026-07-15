from __future__ import annotations

import multiprocessing
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuditask.errors import WudiTaskError
from wuditask.gitops import GitCoordinator, default_cache_root
from wuditask.repository import TaskRepository

from tests.helpers import add_task, git, make_hub_origin


def _hold_snapshot(remote: str, cache_root: str, sender: object) -> None:
    try:
        coordinator = GitCoordinator(
            remote=remote,
            branch="main",
            cache_root=Path(cache_root),
        )
        with coordinator.snapshot() as repository:
            sender.send(str(repository.root))
            sender.close()
            time.sleep(120)
    finally:
        sender.close()


class RecordingCoordinator(GitCoordinator):
    def __init__(self, **kwargs: object) -> None:
        self.commands: list[list[str]] = []
        super().__init__(**kwargs)

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        allowed: set[int] | None = {0},
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return super()._run(command, cwd=cwd, allowed=allowed)


class GitCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.origin = make_hub_origin(self.base)
        self.cache_root = self.base / "cache"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_cache_root_honors_xdg_and_explicit_home(self) -> None:
        xdg = self.base / "xdg-cache"
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(xdg)}):
            self.assertEqual((xdg / "wuditask").resolve(), default_cache_root())
        self.assertEqual(
            (self.base / "home" / ".cache" / "wuditask").resolve(),
            default_cache_root(home=self.base / "home"),
        )

    def test_snapshots_reuse_one_bare_cache_without_cloning(self) -> None:
        coordinator = RecordingCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        with coordinator.snapshot() as repository:
            first_checkout = repository.root
            self.assertTrue((first_checkout / "hub.json").is_file())
        marker = coordinator.cache_path / "reuse-marker"
        marker.write_text("preserved\n", encoding="utf-8")
        task_id = "WDT-20260711T120010Z-BBBBBB"
        seed = self.base / "hub-seed"
        add_task(TaskRepository(seed), task_id, title="Fetched later")
        git(["add", "data"], seed)
        git(["commit", "-m", "add later task"], seed)
        git(["push", "origin", "main"], seed)
        second_coordinator = RecordingCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )
        with second_coordinator.snapshot() as repository:
            second_checkout = repository.root
            self.assertTrue((second_checkout / "hub.json").is_file())
            self.assertIn(task_id, repository.load_index().open)

        self.assertEqual(coordinator.cache_path, second_coordinator.cache_path)
        self.assertNotEqual(first_checkout, second_checkout)
        self.assertFalse(first_checkout.exists())
        self.assertFalse(second_checkout.exists())
        self.assertEqual("preserved\n", marker.read_text(encoding="utf-8"))
        self.assertEqual(
            "true",
            subprocess.run(
                ["git", "rev-parse", "--is-bare-repository"],
                cwd=coordinator.cache_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        )
        self.assertFalse(
            any(
                command[:2] == ["git", "clone"]
                for command in coordinator.commands + second_coordinator.commands
            )
        )
        self.assertEqual([], list((self.cache_root / "operations").iterdir()))

    def test_remote_and_branch_pairs_use_separate_caches(self) -> None:
        subprocess.run(
            ["git", "branch", "queue", "main"],
            cwd=self.origin,
            check=True,
            capture_output=True,
            text=True,
        )
        other_origin = make_hub_origin(self.base, name="other")
        coordinators = (
            GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.cache_root,
            ),
            GitCoordinator(
                remote=str(self.origin),
                branch="queue",
                cache_root=self.cache_root,
            ),
            GitCoordinator(
                remote=str(other_origin),
                branch="main",
                cache_root=self.cache_root,
            ),
        )

        for coordinator in coordinators:
            with coordinator.snapshot() as repository:
                self.assertTrue(repository.manifest_path.is_file())

        self.assertEqual(3, len({item.cache_path for item in coordinators}))
        for coordinator in coordinators:
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=coordinator.cache_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(coordinator.remote, remote)

    def test_snapshot_failure_removes_the_operation_worktree(self) -> None:
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )
        checkout: Path | None = None

        with self.assertRaisesRegex(RuntimeError, "stop"):
            with coordinator.snapshot() as repository:
                checkout = repository.root
                raise RuntimeError("stop")

        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertFalse(checkout.exists())
        self.assertEqual([], list((self.cache_root / "operations").iterdir()))
        worktree_lines = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=coordinator.cache_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertEqual(
            [f"worktree {coordinator.cache_path}", "bare", ""],
            worktree_lines,
        )

    def test_invalid_cache_returns_an_actionable_error(self) -> None:
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )
        coordinator.cache_path.parent.mkdir(parents=True)
        coordinator.cache_path.write_text("not a repository\n", encoding="utf-8")

        with self.assertRaises(WudiTaskError) as raised:
            with coordinator.snapshot():
                pass

        self.assertEqual("hub_cache_invalid", raised.exception.code)
        self.assertEqual(
            str(coordinator.cache_path),
            raised.exception.details["cache"],
        )

    def test_cache_reuse_ignores_git_url_rewrite_output(self) -> None:
        configured_remote = "https://example.test/acme/wuditask-hub.git"
        global_config = self.base / "gitconfig"
        git(
            [
                "config",
                "--file",
                str(global_config),
                f"url.{self.origin}.insteadOf",
                configured_remote,
            ],
            self.base,
        )
        environment = {
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        with mock.patch.dict(os.environ, environment):
            first = GitCoordinator(
                remote=configured_remote,
                branch="main",
                cache_root=self.cache_root,
            )
            with first.snapshot() as repository:
                self.assertTrue(repository.manifest_path.is_file())
            second = GitCoordinator(
                remote=configured_remote,
                branch="main",
                cache_root=self.cache_root,
            )
            with second.snapshot() as repository:
                self.assertTrue(repository.manifest_path.is_file())

        stored = subprocess.run(
            ["git", "config", "--local", "--get", "remote.origin.url"],
            cwd=first.cache_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(configured_remote, stored)

    def test_next_command_reaps_a_worktree_left_by_a_killed_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_hold_snapshot,
            args=(str(self.origin), str(self.cache_root), sender),
        )
        process.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(15), "child did not create a worktree")
            abandoned = Path(receiver.recv())
            self.assertTrue(abandoned.is_dir())
            process.kill()
            process.join(timeout=15)
            self.assertFalse(process.is_alive())
            self.assertNotEqual(0, process.exitcode)

            coordinator = GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.cache_root,
            )
            with coordinator.snapshot() as repository:
                self.assertTrue(repository.manifest_path.is_file())
                self.assertFalse(abandoned.exists())
        finally:
            receiver.close()
            if process.is_alive():
                process.kill()
                process.join(timeout=15)

        self.assertEqual([], list((self.cache_root / "operations").iterdir()))

    def test_cache_io_failure_is_a_structured_error(self) -> None:
        invalid_root = self.base / "cache-file"
        invalid_root.write_text("not a directory\n", encoding="utf-8")
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=invalid_root,
        )

        with self.assertRaises(WudiTaskError) as raised:
            with coordinator.snapshot():
                pass

        self.assertEqual("hub_cache_io_failed", raised.exception.code)
        self.assertEqual(
            str(invalid_root.resolve()),
            raised.exception.details["cache_root"],
        )

    def test_interrupted_cache_initialization_is_retriable(self) -> None:
        class InterruptedCoordinator(GitCoordinator):
            fail_remote_add = True

            def _run(
                self,
                command: list[str],
                *,
                cwd: Path,
                allowed: set[int] | None = {0},
            ) -> subprocess.CompletedProcess[str]:
                if self.fail_remote_add and command[:4] == [
                    "git",
                    "remote",
                    "add",
                    "origin",
                ]:
                    self.fail_remote_add = False
                    raise OSError(5, "simulated cache interruption", str(cwd))
                return super()._run(command, cwd=cwd, allowed=allowed)

        coordinator = InterruptedCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        with self.assertRaises(WudiTaskError) as raised:
            with coordinator.snapshot():
                pass
        self.assertEqual("hub_cache_io_failed", raised.exception.code)
        self.assertFalse(coordinator.cache_path.exists())
        self.assertEqual(
            [],
            list(coordinator.cache_path.parent.glob(".*.tmp")),
        )

        with coordinator.snapshot() as repository:
            self.assertTrue(repository.manifest_path.is_file())

    def test_snapshot_preserves_an_os_error_from_the_caller(self) -> None:
        coordinator = GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        )

        with self.assertRaises(OSError) as raised:
            with coordinator.snapshot():
                raise OSError(13, "caller output is not writable", "/outside/output")

        self.assertNotIsInstance(raised.exception, WudiTaskError)
        self.assertEqual("/outside/output", raised.exception.filename)
        self.assertEqual([], list((self.cache_root / "operations").iterdir()))


if __name__ == "__main__":
    unittest.main()
