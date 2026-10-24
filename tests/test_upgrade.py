from __future__ import annotations

import argparse
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import make_hub_origin, git, add_task, make_repository, ACTOR, RUN_ID
from tests.test_install import make_tool
from tests.test_personal_coordination import ticket
from wuditask.execution_setup import prepare_branch, upgrade
from wuditask.execution_cli import coordinator
from wuditask.errors import WudiTaskError
from wuditask.install import install_agent_access
from wuditask.util import atomic_write_json
from wuditask.gitops import GitCoordinator

ROOT = Path(__file__).resolve().parents[1]
ACTOR_INFO = {"actor_id": "12", "login": "alice"}


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = make_hub_origin(self.base)
        self.seed = self.base / "hub-seed"
        self.task_id = "WDT-20260711T120001Z-AAAAAA"
        old = make_repository(self.seed)
        add_task(old, self.task_id)
        self.legacy_path = self.seed / "data/open" / f"{self.task_id}.json"
        record = json.loads(self.legacy_path.read_text())
        record["active_agents"] = [{"login": "alice", "run_id": RUN_ID}]
        atomic_write_json(self.legacy_path, record)
        self.old_bytes = self.legacy_path.read_bytes()
        git(["add", "data"], self.seed)
        git(["commit", "-m", "old active task"], self.seed)
        git(["push"], self.seed)
        self.coord = coordinator({"remote": str(self.remote), "branch": "main"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_upgrade_archives_active_legacy_records_without_stop_proof(self):
        result = prepare_branch(str(self.remote), "main", ACTOR_INFO)
        self.assertFalse(result["branch_created"])
        self.assertFalse(result["storage"]["migration"]["stop_verified"])
        with self.coord.snapshot() as repo:
            archived = repo.root / "data/legacy/open" / self.legacy_path.name
            self.assertEqual(self.old_bytes, archived.read_bytes())
            self.assertFalse((repo.root / "data/open").exists())
            self.assertEqual(1, len(result["storage"]["migration"]["active"]))
            with self.assertRaises(WudiTaskError):
                make_repository(repo.root).load_index()
        branches = git(
            ["for-each-ref", "--format=%(refname)", "refs/heads"], self.remote
        ).stdout
        self.assertEqual("refs/heads/main\n", branches)

    def test_repeated_upgrade_preserves_current_personal_claims(self):
        prepare_branch(str(self.remote), "main", ACTOR_INFO)
        t = ticket()
        self.coord.write(lambda r: r.acquire(t), ACTOR, lambda _: "claim")
        self.assertFalse(
            prepare_branch(str(self.remote), "main", ACTOR_INFO)["storage"]["changed"]
        )
        with self.coord.snapshot() as repo:
            repo.assert_current(t)

    def test_concurrent_upgrade_converges_without_double_archive(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        original = GitCoordinator._push

        def first_push(coord, root):
            barrier.wait(10)
            return original(coord, root)

        # Both migration commits contain the same legacy bytes. A losing client
        # replays against the winner and sees the already-upgraded marker.
        def attempt():
            try:
                results.append(prepare_branch(str(self.remote), "main", ACTOR_INFO))
            except Exception as e:
                errors.append(e)

        with patch.object(GitCoordinator, "_push", first_push):
            threads = [threading.Thread(target=attempt) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(20)
                self.assertFalse(t.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        with self.coord.snapshot() as repo:
            self.assertEqual(
                self.old_bytes,
                (repo.root / "data/legacy/open" / self.legacy_path.name).read_bytes(),
            )

    def test_upgrade_two_repo_install_and_remove_managed_old_skills(self):
        home = self.base / "home"
        tool = make_tool(self.base / "tool-fixture", content_source=ROOT)
        install_agent_access(tool, hub_remote=str(self.remote), home=home)
        for product in (".agents", ".claude"):
            base = home / product / "skills"
            for old in ("wuditask-add", "wuditask-selfupdate", "wuditask-coordinate"):
                (base / old).symlink_to(tool / ".agents/skills" / old)
            (base / "unrelated").write_text("keep me")
        args = argparse.Namespace(coord_command="upgrade", remote=None, branch=None)
        with patch.object(Path, "home", return_value=home):
            result = upgrade(args, ACTOR_INFO, tool)
            self.assertEqual("$wuditask", result["skill"])
            self.assertEqual("main", result["branch"])
            self.assertFalse(upgrade(args, ACTOR_INFO, tool)["storage"]["changed"])
        for product in (".agents", ".claude"):
            base = home / product / "skills"
            self.assertEqual(
                {"wuditask", "unrelated"}, {p.name for p in base.iterdir()}
            )
        for name in ("config.json", "personal/config.json"):
            config = json.loads((home / ".wuditask" / name).read_text())
            self.assertEqual("main", config.get("branch", config.get("hub_branch")))

    def test_empty_temporary_branch_can_rebind_but_history_cannot_be_lost(self):
        prepare_branch(str(self.remote), "main", ACTOR_INFO)
        git(["fetch", "origin", "main"], self.seed)
        git(["push", "origin", "origin/main:refs/heads/personal-execution"], self.seed)
        home = self.base / "home"
        tool = make_tool(self.base / "tool-fixture", content_source=ROOT)
        install_agent_access(
            tool,
            hub_remote=str(self.remote),
            hub_branch="personal-execution",
            home=home,
        )
        oldconfig = dict(
            actor_id="12",
            remote=str(self.remote),
            branch="personal-execution",
            environment="env",
        )
        atomic_write_json(home / ".wuditask/personal/config.json", oldconfig)
        args = argparse.Namespace(coord_command="upgrade", remote=None, branch=None)
        with patch.object(Path, "home", return_value=home):
            self.assertEqual("main", upgrade(args, ACTOR_INFO, tool)["branch"])
        source = coordinator(oldconfig)
        with source.snapshot() as repo:
            with self.assertRaises(WudiTaskError):
                repo.acquire(ticket())
        # A retry after local registration failed can follow the same relocation.
        atomic_write_json(home / ".wuditask/personal/config.json", oldconfig)
        with patch.object(Path, "home", return_value=home):
            self.assertEqual("main", upgrade(args, ACTOR_INFO, tool)["branch"])

    def test_nonempty_branch_is_not_silently_rebound(self):
        prepare_branch(str(self.remote), "main", ACTOR_INFO)
        git(["fetch", "origin", "main"], self.seed)
        git(["push", "origin", "origin/main:refs/heads/personal-execution"], self.seed)
        home = self.base / "home"
        tool = make_tool(self.base / "tool-fixture", content_source=ROOT)
        install_agent_access(
            tool,
            hub_remote=str(self.remote),
            hub_branch="personal-execution",
            home=home,
        )
        oldconfig = dict(
            actor_id="12",
            remote=str(self.remote),
            branch="personal-execution",
            environment="env",
        )
        atomic_write_json(home / ".wuditask/personal/config.json", oldconfig)
        source = coordinator(oldconfig)
        t = ticket()
        source.write(lambda r: r.acquire(t), ACTOR, lambda _: "claim")
        args = argparse.Namespace(coord_command="upgrade", remote=None, branch=None)
        with patch.object(Path, "home", return_value=home):
            with self.assertRaises(WudiTaskError) as error:
                upgrade(args, ACTOR_INFO, tool)
        self.assertEqual("execution_history_requires_transfer", error.exception.code)
        with source.snapshot() as repo:
            repo.assert_current(t)
        self.assertEqual(
            oldconfig, json.loads((home / ".wuditask/personal/config.json").read_text())
        )


class UpgradeEntryTests(unittest.TestCase):
    def test_tool_updates_before_fresh_interpreter_upgrades_storage(self):
        from wuditask.upgrade import upgrade_installation
        import subprocess

        calls = []

        def updated(root):
            calls.append("tool")
            return {"status": "updated"}

        def launched(argv, **kwargs):
            calls.append("storage")
            self.assertEqual(["--json", "coord", "upgrade"], argv[-3:])
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")

        with (
            patch("wuditask.upgrade.self_update", side_effect=updated),
            patch("wuditask.upgrade.subprocess.run", side_effect=launched),
        ):
            self.assertTrue(upgrade_installation(Path("/tool"))["coordination"]["ok"])
        self.assertEqual(["tool", "storage"], calls)

    def test_tool_failure_does_not_start_storage_upgrade(self):
        from wuditask.upgrade import upgrade_installation

        with (
            patch(
                "wuditask.upgrade.self_update",
                side_effect=WudiTaskError("dirty", "keep changes"),
            ),
            patch("wuditask.upgrade.subprocess.run") as launch,
        ):
            with self.assertRaises(WudiTaskError):
                upgrade_installation(Path("/tool"))
            launch.assert_not_called()
