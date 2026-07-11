from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.install import install_agent_access

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "wuditask",
    "wuditask-add",
    "wuditask-archive",
    "wuditask-dep-check",
    "wuditask-execute",
    "wuditask-inspect",
    "wuditask-install",
    "wuditask-release",
    "wuditask-selfupdate",
}


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

    def test_installer_discovers_only_direct_skill_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = root / "hub"
            home = root / "home"
            (hub / "tools").mkdir(parents=True)
            (hub / "tools" / "wuditask.py").write_text("#!/usr/bin/env python3\n")
            skills_root = hub / ".agents" / "skills"
            for name in sorted(EXPECTED_SKILLS | {"wuditask-extra"}):
                skill = skills_root / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            nested = skills_root / "wuditask-extra" / "references"
            nested.mkdir()
            (nested / "SKILL.md").write_text("not a standalone skill\n")
            (skills_root / "not-a-skill").mkdir()

            result = install_agent_access(hub, home=home)

            self.assertEqual(
                sorted(EXPECTED_SKILLS | {"wuditask-extra"}),
                result["skills"],
            )
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertEqual(
                    result["skills"],
                    sorted(path.name for path in base.iterdir()),
                )
                unrelated_target = root / f"unrelated-{base.parent.name}"
                unrelated_target.mkdir()
                (base / "personal-skill").symlink_to(
                    unrelated_target,
                    target_is_directory=True,
                )
                (base / "notes.txt").write_text("user-owned\n")

            (skills_root / "wuditask-extra" / "SKILL.md").unlink()
            reconciled = install_agent_access(hub, home=home)
            self.assertEqual(sorted(EXPECTED_SKILLS), reconciled["skills"])
            self.assertEqual(2, len(reconciled["removed_links"]))
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertFalse((base / "wuditask-extra").exists())
                self.assertTrue((base / "personal-skill").is_symlink())
                self.assertEqual("user-owned", (base / "notes.txt").read_text().strip())

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            install_agent_access(ROOT, home=home)
            second = install_agent_access(ROOT, home=home)
            self.assertTrue(all(not link["changed"] for link in second["links"]))

    def test_installer_rejects_a_missing_required_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hub = root / "hub"
            (hub / "tools").mkdir(parents=True)
            (hub / "tools" / "wuditask.py").write_text("#!/usr/bin/env python3\n")
            skills_root = hub / ".agents" / "skills"
            missing = "wuditask-release"
            for name in sorted(EXPECTED_SKILLS - {missing}):
                skill = skills_root / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(hub, home=root / "home")

            self.assertEqual("invalid_hub_clone", raised.exception.code)
            self.assertEqual([missing], raised.exception.details["missing_skills"])


if __name__ == "__main__":
    unittest.main()
