from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.install import install_agent_access

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "wuditask-add",
    "wuditask-archive",
    "wuditask-dep-check",
    "wuditask-execute",
    "wuditask-install",
    "wuditask-list",
    "wuditask-release",
    "wuditask-selfupdate",
    "wuditask-show",
}


def make_hub(root: Path, skills: set[str] | None = None) -> Path:
    hub = root / "hub"
    (hub / "tools").mkdir(parents=True)
    (hub / "tools" / "wuditask.py").write_text("#!/usr/bin/env python3\n")
    for name in sorted(skills or EXPECTED_SKILLS):
        skill = hub / ".agents" / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return hub


class InstallTests(unittest.TestCase):
    def test_installer_registers_all_skills_for_both_agent_products(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            result = install_agent_access(ROOT, home=home)
            config = json.loads((home / ".wuditask" / "config.json").read_text())
            skills_root = ROOT / ".agents" / "skills"
            skill_names = sorted(
                path.name
                for path in skills_root.iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
            self.assertEqual(EXPECTED_SKILLS, set(skill_names))
            self.assertEqual(skill_names, result["skills"])

            self.assertEqual(str(ROOT.resolve()), config["hub_path"])
            self.assertEqual(
                str((home / ".wuditask" / "config.json").resolve()),
                result["config"],
            )
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertEqual(
                    skill_names,
                    sorted(path.name for path in base.iterdir()),
                )
                for skill_name in skill_names:
                    installed = base / skill_name
                    self.assertTrue(installed.is_symlink())
                    self.assertEqual(
                        (skills_root / skill_name).resolve(),
                        installed.resolve(),
                    )
                self.assertIn(
                    "Prefer an existing GitHub Issue or PR",
                    (base / "wuditask-add" / "SKILL.md").read_text(),
                )
                self.assertIn(
                    "Do not create a GitHub Issue for this maintenance request",
                    (base / "wuditask-selfupdate" / "SKILL.md").read_text(),
                )
            self.assertTrue((home / ".local" / "bin" / "wuditask").is_symlink())
            self.assertEqual(len(skill_names) * 2 + 1, len(result["links"]))

    def test_installer_rejects_unexpected_skills_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = make_hub(root, EXPECTED_SKILLS | {"wuditask-extra"})
            home = root / "home"

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(hub, home=home)

            self.assertEqual("invalid_hub_clone", raised.exception.code)
            self.assertEqual(
                ["wuditask-extra"],
                raised.exception.details["unexpected_skills"],
            )
            self.assertFalse(home.exists())

    def test_installer_removes_only_registered_stale_skill_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = make_hub(root)
            home = root / "home"
            install_agent_access(hub, home=home)
            stale_source = hub / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                (base / "wuditask").symlink_to(
                    stale_source,
                    target_is_directory=True,
                )
                unrelated_target = root / f"unrelated-{base.parent.name}"
                unrelated_target.mkdir()
                (base / "personal-skill").symlink_to(
                    unrelated_target,
                    target_is_directory=True,
                )
                (base / "notes.txt").write_text("user-owned\n")

            reconciled = install_agent_access(hub, home=home)
            self.assertEqual(sorted(EXPECTED_SKILLS), reconciled["skills"])
            self.assertEqual(2, len(reconciled["removed_links"]))
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertFalse((base / "wuditask").exists())
                self.assertTrue((base / "personal-skill").is_symlink())
                self.assertEqual("user-owned", (base / "notes.txt").read_text().strip())

    def test_installer_retargets_links_from_registered_previous_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_hub = make_hub(root / "old")
            new_hub = make_hub(root / "new")
            home = root / "home"
            install_agent_access(old_hub, home=home)
            stale_source = old_hub / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                (base / "wuditask").symlink_to(
                    stale_source,
                    target_is_directory=True,
                )

            result = install_agent_access(new_hub, home=home)

            self.assertEqual(2, len(result["removed_links"]))
            config = json.loads((home / ".wuditask" / "config.json").read_text())
            self.assertEqual(str(new_hub.resolve()), config["hub_path"])
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertFalse((base / "wuditask").exists())
                for name in EXPECTED_SKILLS:
                    self.assertEqual(
                        (new_hub / ".agents" / "skills" / name).resolve(),
                        (base / name).resolve(),
                    )
            self.assertEqual(
                (new_hub / "tools" / "wuditask.py").resolve(),
                (home / ".local" / "bin" / "wuditask").resolve(),
            )

    def test_install_conflict_does_not_partially_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = make_hub(root)
            home = root / "home"
            agent_skills = home / ".agents" / "skills"
            agent_skills.mkdir(parents=True)
            stale_source = hub / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            (agent_skills / "wuditask").symlink_to(
                stale_source,
                target_is_directory=True,
            )
            conflict = agent_skills / "wuditask-list"
            conflict.write_text("user-owned\n")

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(hub, home=home)

            self.assertEqual("install_path_exists", raised.exception.code)
            self.assertTrue((agent_skills / "wuditask").is_symlink())
            self.assertEqual("user-owned", conflict.read_text().strip())
            self.assertFalse((agent_skills / "wuditask-add").exists())
            self.assertFalse((home / ".claude").exists())
            self.assertFalse((home / ".wuditask" / "config.json").exists())

    def test_invalid_product_parent_does_not_partially_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = make_hub(root)
            home = root / "home"
            agent_skills = home / ".agents" / "skills"
            agent_skills.mkdir(parents=True)
            stale_source = hub / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            (agent_skills / "wuditask").symlink_to(
                stale_source,
                target_is_directory=True,
            )
            (home / ".claude").write_text("user-owned\n")

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(hub, home=home)

            self.assertEqual("install_path_exists", raised.exception.code)
            self.assertTrue((agent_skills / "wuditask").is_symlink())
            self.assertFalse((agent_skills / "wuditask-add").exists())
            self.assertEqual("user-owned", (home / ".claude").read_text().strip())
            self.assertFalse((home / ".wuditask" / "config.json").exists())

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            install_agent_access(ROOT, home=home)
            second = install_agent_access(ROOT, home=home)
            self.assertTrue(all(not link["changed"] for link in second["links"]))

    def test_installer_rejects_a_missing_required_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = "wuditask-release"
            hub = make_hub(root, EXPECTED_SKILLS - {missing})

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(hub, home=root / "home")

            self.assertEqual("invalid_hub_clone", raised.exception.code)
            self.assertEqual([missing], raised.exception.details["missing_skills"])
            self.assertEqual([], raised.exception.details["unexpected_skills"])


if __name__ == "__main__":
    unittest.main()
