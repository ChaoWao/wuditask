from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.selfupdate import self_update

from tests.helpers import git


class SelfUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.origin = self.base / "origin.git"
        git(["init", "--bare", "--initial-branch=main", str(self.origin)], self.base)

        self.seed = self.base / "seed"
        self.seed.mkdir()
        git(["init", "-b", "main"], self.seed)
        git(["config", "user.name", "seed"], self.seed)
        git(["config", "user.email", "seed@example.invalid"], self.seed)
        (self.seed / "tools").mkdir()
        (self.seed / "tests").mkdir()
        (self.seed / ".agents" / "skills" / "wuditask-install").mkdir(parents=True)
        (self.seed / ".agents" / "skills" / "wuditask-install" / "SKILL.md").write_text(
            "---\nname: wuditask-install\n---\n",
            encoding="utf-8",
        )
        (self.seed / ".agents" / "skills" / ".draft").mkdir(parents=True)
        (self.seed / ".agents" / "skills" / ".draft" / "SKILL.md").write_text(
            "---\nname: draft\n---\n",
            encoding="utf-8",
        )
        (self.seed / "tools" / "wuditask.py").write_text(
            "#!/usr/bin/env python3\nprint('{\"ok\": true}')\n",
            encoding="utf-8",
        )
        (self.seed / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_candidate(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.seed / "VERSION").write_text("1\n", encoding="utf-8")
        git(["add", "."], self.seed)
        git(["commit", "-m", "version one"], self.seed)
        git(["remote", "add", "origin", str(self.origin)], self.seed)
        git(["push", "-u", "origin", "main"], self.seed)

        self.client = self.base / "client"
        git(["clone", str(self.origin), str(self.client)], self.base)
        self.home = self.base / "home"
        for product_path in (
            self.home / ".agents" / "skills",
            self.home / ".claude" / "skills",
        ):
            product_path.mkdir(parents=True)
            (product_path / "wuditask-install").symlink_to(
                self.client / ".agents" / "skills" / "wuditask-install",
                target_is_directory=True,
            )

        (self.seed / "VERSION").write_text("2\n", encoding="utf-8")
        git(["add", "VERSION"], self.seed)
        git(["commit", "-m", "version two"], self.seed)
        git(["push", "origin", "main"], self.seed)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def update(self, *, check_only: bool = False) -> dict[str, object]:
        return self_update(self.client, check_only=check_only, home=self.home)

    def test_check_reports_update_without_merging(self) -> None:
        result = self.update(check_only=True)

        self.assertEqual("update_available", result["status"])
        self.assertEqual("1", (self.client / "VERSION").read_text().strip())
        self.assertFalse(result["reinstall_required"])

    def test_update_verifies_then_fast_forwards(self) -> None:
        result = self.update()

        self.assertEqual("updated", result["status"])
        self.assertEqual("2", (self.client / "VERSION").read_text().strip())
        self.assertEqual("passed", result["verification"]["validate"])
        self.assertEqual("passed", result["verification"]["tests"])
        self.assertFalse(result["reinstall_required"])

        current = self.update()
        self.assertEqual("up_to_date", current["status"])
        self.assertFalse(current["reinstall_required"])

    def test_update_reports_when_skill_inventory_needs_reinstall(self) -> None:
        added_skill = self.seed / ".agents" / "skills" / "wuditask-add"
        added_skill.mkdir()
        (added_skill / "SKILL.md").write_text(
            "---\nname: wuditask-add\n---\n",
            encoding="utf-8",
        )
        git(["add", ".agents/skills/wuditask-add"], self.seed)
        git(["commit", "-m", "add operation skill"], self.seed)
        git(["push", "origin", "main"], self.seed)

        check = self.update(check_only=True)
        self.assertFalse(check["reinstall_required"])
        self.assertTrue(check["reinstall_required_after_update"])

        updated = self.update()
        self.assertTrue(updated["reinstall_required"])
        self.assertTrue((self.client / ".agents" / "skills" / "wuditask-add").is_dir())

        still_missing = self.update()
        self.assertEqual("up_to_date", still_missing["status"])
        self.assertTrue(still_missing["reinstall_required"])

        for product_path in (
            self.home / ".agents" / "skills",
            self.home / ".claude" / "skills",
        ):
            (product_path / "wuditask-add").symlink_to(
                self.client / ".agents" / "skills" / "wuditask-add",
                target_is_directory=True,
            )
        reconciled = self.update()
        self.assertFalse(reconciled["reinstall_required"])

    def test_update_refuses_dirty_worktree(self) -> None:
        (self.client / "VERSION").write_text("local change\n", encoding="utf-8")

        with self.assertRaises(WudiTaskError) as raised:
            self.update()

        self.assertEqual("selfupdate_dirty_worktree", raised.exception.code)
        self.assertEqual("local change", (self.client / "VERSION").read_text().strip())

    def test_failed_candidate_does_not_change_installed_clone(self) -> None:
        (self.seed / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_candidate(self):\n"
            "        self.fail('candidate is broken')\n",
            encoding="utf-8",
        )
        git(["add", "tests/test_smoke.py"], self.seed)
        git(["commit", "-m", "broken candidate"], self.seed)
        git(["push", "origin", "main"], self.seed)

        with self.assertRaises(WudiTaskError) as raised:
            self.update()

        self.assertEqual("selfupdate_candidate_failed", raised.exception.code)
        self.assertEqual("1", (self.client / "VERSION").read_text().strip())


if __name__ == "__main__":
    unittest.main()
