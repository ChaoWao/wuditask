from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
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

    def test_help_router_lists_every_operation_skill(self) -> None:
        router = (SKILLS_ROOT / "wuditask" / "SKILL.md").read_text(encoding="utf-8")
        for name in sorted(EXPECTED_SKILLS - {"wuditask"}):
            self.assertIn(f"${name}", router)

    def test_add_and_selfupdate_policy_invariants(self) -> None:
        add = (SKILLS_ROOT / "wuditask-add" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("canonical Issue or PR URL", add)
        self.assertIn("Do not create an Issue in the WudiTask hub", add)
        self.assertLess(
            add.index("## Build the execution contract"),
            add.index("## Establish the canonical description"),
        )
        self.assertIn("do not silently fall back to text", add)

        selfupdate = (SKILLS_ROOT / "wuditask-selfupdate" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "Do not create a GitHub Issue for this maintenance request",
            selfupdate,
        )
        self.assertIn(
            "Do not run WudiTask `add`, `execute`, `archive`, or `release`",
            selfupdate,
        )
        self.assertIn("~/.wuditask/worktrees/<slug>", selfupdate)
        self.assertIn("reinstall_required=true", selfupdate)


if __name__ == "__main__":
    unittest.main()
