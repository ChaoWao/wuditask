from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
EXPECTED_SKILLS = {"wuditask"}


def _frontmatter_value(text: str, key: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            break
        prefix = f"{key}:"
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


class SkillTests(unittest.TestCase):
    def test_complete_skill_suite_and_metadata(self) -> None:
        skill_dirs = {
            path.name
            for path in SKILLS_ROOT.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        self.assertEqual(EXPECTED_SKILLS, skill_dirs)

        for name in sorted(EXPECTED_SKILLS):
            skill_dir = SKILLS_ROOT / name
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            metadata = (skill_dir / "agents" / "openai.yaml").read_text(
                encoding="utf-8"
            )
            self.assertEqual(name, _frontmatter_value(skill_text, "name"))
            self.assertTrue(_frontmatter_value(skill_text, "description"))
            self.assertNotIn("TODO", skill_text)
            self.assertIn("$wuditask", metadata)
            self.assertIn(f"${name}", metadata)
            self.assertNotIn("hub_path", skill_text)

    def test_relative_skill_references_resolve(self) -> None:
        for name in sorted(EXPECTED_SKILLS):
            skill_file = SKILLS_ROOT / name / "SKILL.md"
            skill_text = skill_file.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", skill_text):
                if "://" in target or target.startswith("#"):
                    continue
                self.assertTrue(
                    (skill_file.parent / target).resolve().is_file(),
                    f"{name} has a missing reference: {target}",
                )

    def test_upgrade_and_coordination_share_the_entry(self):
        skill = (SKILLS_ROOT / "wuditask/SKILL.md").read_text()
        for reference in ("upgrade.md", "coordinate.md", "maintain.md"):
            self.assertIn(reference, skill)
        upgrade = (SKILLS_ROOT / "wuditask/references/upgrade.md").read_text()
        self.assertIn("selfupdate", upgrade)
        self.assertIn("coord upgrade", upgrade)
        self.assertIn("data/legacy/", upgrade)
        self.assertIn("no execution claims", upgrade)
        coordinate = (SKILLS_ROOT / "wuditask/references/coordinate.md").read_text()
        self.assertIn("expected-run-id", coordinate)
        self.assertIn("No JSON stop receipt", coordinate)


if __name__ == "__main__":
    unittest.main()
