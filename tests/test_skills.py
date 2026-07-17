from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
EXPECTED_SKILLS = {
    "wuditask-add",
    "wuditask-archive",
    "wuditask-assign",
    "wuditask-check",
    "wuditask-delete",
    "wuditask-execute",
    "wuditask-install",
    "wuditask-list",
    "wuditask-release",
    "wuditask-selfupdate",
    "wuditask-show",
    "wuditask-unassign",
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

    def test_list_and_show_have_distinct_read_only_contracts(self) -> None:
        task_list = (SKILLS_ROOT / "wuditask-list" / "SKILL.md").read_text()
        task_show = (SKILLS_ROOT / "wuditask-show" / "SKILL.md").read_text()
        self.assertIn("--scope open", task_list)
        self.assertIn("show TASK_ID", task_show)
        self.assertIn("read-only `list` command", task_list)
        self.assertIn("read-only `show` command", task_show)

    def test_check_replaces_dependency_and_reconcile_skills(self) -> None:
        check = (SKILLS_ROOT / "wuditask-check" / "SKILL.md").read_text()
        self.assertIn("read-only `check` command", check)
        self.assertIn("check [TASK_ID]", check)
        self.assertIn("dependencies", check)
        self.assertIn("active agents", check)
        self.assertFalse((SKILLS_ROOT / "wuditask-dep-check").exists())
        self.assertFalse((SKILLS_ROOT / "wuditask-reconcile").exists())

    def test_assignment_and_execution_are_independent(self) -> None:
        assign = (SKILLS_ROOT / "wuditask-assign" / "SKILL.md").read_text()
        unassign = (SKILLS_ROOT / "wuditask-unassign" / "SKILL.md").read_text()
        execute = (SKILLS_ROOT / "wuditask-execute" / "SKILL.md").read_text()
        release = (SKILLS_ROOT / "wuditask-release" / "SKILL.md").read_text()

        self.assertIn("assign TASK_ID --to LOGIN", assign)
        self.assertIn("explicitly names and authorizes", assign)
        self.assertIn("unassign TASK_ID --from LOGIN", unassign)
        self.assertIn("explicitly names and authorizes", unassign)
        self.assertIn("never adds a Hub `active_agents` entry", assign)
        self.assertIn("first self-assigns the authenticated login", execute)
        self.assertIn("do not roll the assignment back", execute)
        self.assertRegex(execute, r"Multiple\s+different owner logins")
        self.assertIn("run_id", execute)
        self.assertIn("--run-id RUN_ID", release)
        self.assertIn("never removes an Issue", release)

    def test_add_and_archive_keep_acceptance_in_the_source(self) -> None:
        add = (SKILLS_ROOT / "wuditask-add" / "SKILL.md").read_text()
        archive = (SKILLS_ROOT / "wuditask-archive" / "SKILL.md").read_text()

        self.assertIn("## Establish the source first", add)
        self.assertIn("acceptance requirements before", add)
        self.assertNotIn("--accept", add)
        self.assertNotIn("--text-source-reason", add)
        self.assertIn("only narrative and", archive)
        self.assertIn("acceptance contract", archive)
        self.assertIn("--run-id RUN_ID", archive)
        self.assertIn("--evidence", archive)
        self.assertNotIn("AC-N", archive)

    def test_delete_is_explicit_and_preserves_dependency_integrity(self) -> None:
        delete = (SKILLS_ROOT / "wuditask-delete" / "SKILL.md").read_text()
        self.assertIn("explicitly", delete)
        self.assertIn("--reason", delete)
        self.assertIn("reverse dependency", delete)
        self.assertIn("data/deletions/", delete)
        self.assertIn("Git history", delete)
        self.assertIn("not erased", delete)
        self.assertIn("absence alone is never confirmation", delete)
        self.assertIn("Never add `--local`", delete)
        self.assertIn("sync.confirmed=true", delete)

    def test_add_and_selfupdate_policy_invariants(self) -> None:
        add = (SKILLS_ROOT / "wuditask-add" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("canonical GitHub source", add)
        self.assertIn("configured Hub repository", add)
        self.assertIn("acceptance requirements", add)
        self.assertIn("temporary authentication or network failure", add)

        selfupdate = (SKILLS_ROOT / "wuditask-selfupdate" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "Do not create a GitHub Issue for this maintenance request",
            selfupdate,
        )
        self.assertIn(
            "`add`, `assign`, `execute`, `release`, `unassign`, `archive`, or `delete`",
            selfupdate,
        )
        self.assertIn("~/.wuditask/worktrees/<slug>", selfupdate)
        self.assertIn("reinstall_required=true", selfupdate)
        self.assertIn("tool_remote", selfupdate)
        self.assertIn("hub_remote", selfupdate)
        self.assertIn(
            "--force-with-lease=refs/heads/<branch>:<observed-oid>",
            selfupdate,
        )
        self.assertIn(
            "Force-push is permitted only for `tool_remote`",
            selfupdate,
        )
        self.assertIn("Never force-push `hub_remote`", selfupdate)
        self.assertIn(
            "A lease rejection must stop the force-push path",
            selfupdate,
        )
        self.assertIn(
            "Never use bare `--force` or bare `--force-with-lease`",
            selfupdate,
        )
        self.assertIn("canonical repository identity", selfupdate)
        self.assertIn(
            "ordinary self-update must remain fail closed",
            selfupdate,
        )
        self.assertIn("separate explicit approval", selfupdate)
        self.assertIn("user explicitly requested a history rewrite", selfupdate)
        self.assertIn(
            "another maintainer's branch, a tag, or a release ref",
            selfupdate,
        )

        install = (SKILLS_ROOT / "wuditask-install" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("--hub-remote", install)
        self.assertIn("tool_path", install)
        self.assertIn("hub_cache", install)
        self.assertIn("XDG_CACHE_HOME", install)
        self.assertIn("isolated operation worktree", install)
        self.assertIn("exactly twelve skills", install)
        self.assertIn("retired dep-check/reconcile links", install)


if __name__ == "__main__":
    unittest.main()
