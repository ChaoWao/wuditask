from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.install import install_agent_access
from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json

from tests.helpers import add_task, git, make_hub_origin

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


def make_tool(
    root: Path,
    skills: set[str] | None = None,
    *,
    content_source: Path | None = None,
) -> Path:
    tool = root / "tool"
    if content_source is not None:
        (tool / "tools").mkdir(parents=True)
        shutil.copy2(
            content_source / "tools" / "wuditask.py",
            tool / "tools" / "wuditask.py",
        )
        shutil.copytree(
            content_source / ".agents" / "skills",
            tool / ".agents" / "skills",
        )
    else:
        (tool / "tools").mkdir(parents=True)
        (tool / "tools" / "wuditask.py").write_text("#!/usr/bin/env python3\n")
        for name in sorted(skills or EXPECTED_SKILLS):
            skill = tool / ".agents" / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    git(["init", "-b", "main"], tool)
    git(["config", "user.name", "tool"], tool)
    git(["config", "user.email", "tool@example.invalid"], tool)
    git(["add", "."], tool)
    git(["commit", "-m", "initialize tool"], tool)
    git(["remote", "add", "origin", "https://example.invalid/wuditask.git"], tool)
    return tool


class InstallTests(unittest.TestCase):
    def test_installer_registers_all_skills_for_both_agent_products(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tool = make_tool(home / "fixture", content_source=ROOT)
            hub_remote = make_hub_origin(home)
            result = install_agent_access(
                tool,
                hub_remote=str(hub_remote),
                home=home,
            )
            config = json.loads((home / ".wuditask" / "config.json").read_text())
            skills_root = tool / ".agents" / "skills"
            skill_names = sorted(
                path.name
                for path in skills_root.iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
            self.assertEqual(EXPECTED_SKILLS, set(skill_names))
            self.assertEqual(skill_names, result["skills"])

            self.assertEqual(2, config["schema_version"])
            self.assertEqual(str(tool.resolve()), config["tool_path"])
            self.assertEqual(str(hub_remote), config["hub_remote"])
            self.assertEqual("main", config["hub_branch"])
            self.assertNotIn("hub_path", config)
            self.assertNotIn("hub_cache", config)
            self.assertEqual(
                str(next((home / ".cache" / "wuditask" / "hubs").iterdir()).resolve()),
                result["hub_cache"],
            )
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
            tool = make_tool(root, EXPECTED_SKILLS | {"wuditask-extra"})
            hub_remote = make_hub_origin(root)
            home = root / "home"

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=home,
                )

            self.assertEqual("invalid_tool_clone", raised.exception.code)
            self.assertEqual(
                ["wuditask-extra"],
                raised.exception.details["unexpected_skills"],
            )
            self.assertFalse(home.exists())

    def test_installer_rejects_incompatible_hub_before_local_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            hub_remote = make_hub_origin(root)
            seed = root / "hub-seed"
            (seed / "hub.json").write_text(
                '{"schema_version": 2, "tool_api_version": 1}\n',
                encoding="utf-8",
            )
            git(["add", "hub.json"], seed)
            git(["commit", "-m", "break hub contract"], seed)
            git(["push", "origin", "main"], seed)
            home = root / "home"

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=home,
                )

            self.assertEqual("invalid_task_data", raised.exception.code)
            self.assertFalse((home / ".wuditask" / "config.json").exists())
            self.assertFalse((home / ".agents").exists())
            self.assertFalse((home / ".claude").exists())
            self.assertFalse((home / ".local").exists())
            self.assertEqual(
                [], list((home / ".cache" / "wuditask" / "operations").iterdir())
            )

    def test_installer_rejects_tool_repository_as_the_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            home = root / "home"

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote="https://example.invalid/wuditask.git",
                    home=home,
                )

            self.assertEqual("hub_matches_tool_repository", raised.exception.code)
            self.assertFalse(home.exists())

            git(
                [
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/Acme/WudiTask.git",
                ],
                tool,
            )
            with self.assertRaises(WudiTaskError) as alternate:
                install_agent_access(
                    tool,
                    hub_remote="git@github.com:acme/wuditask.git",
                    home=home,
                )

            self.assertEqual(
                "hub_matches_tool_repository",
                alternate.exception.code,
            )

    def test_installer_rejects_semantically_invalid_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            hub_remote = make_hub_origin(root)
            seed = root / "hub-seed"
            repository = TaskRepository(seed)
            task_id = "WDT-20260711T120004Z-555555"
            task = add_task(repository, task_id)
            task["dependencies"] = ["WDT-20260711T120005Z-666666"]
            atomic_write_json(repository.open_dir / f"{task_id}.json", task)
            git(["add", "data"], seed)
            git(["commit", "-m", "add task with missing dependency"], seed)
            git(["push", "origin", "main"], seed)
            home = root / "home"

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=home,
                )

            self.assertEqual("invalid_task_data", raised.exception.code)
            self.assertFalse((home / ".wuditask" / "config.json").exists())
            self.assertFalse((home / ".agents").exists())
            self.assertFalse((home / ".claude").exists())
            self.assertFalse((home / ".local").exists())
            self.assertEqual(
                [], list((home / ".cache" / "wuditask" / "operations").iterdir())
            )

    def test_installer_removes_only_registered_stale_skill_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            hub_remote = make_hub_origin(root)
            home = root / "home"
            install_agent_access(tool, hub_remote=str(hub_remote), home=home)
            stale_source = tool / ".agents" / "skills" / "wuditask"
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

            reconciled = install_agent_access(
                tool,
                hub_remote=str(hub_remote),
                home=home,
            )
            self.assertEqual(sorted(EXPECTED_SKILLS), reconciled["skills"])
            self.assertEqual(2, len(reconciled["removed_links"]))
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertFalse((base / "wuditask").exists())
                self.assertTrue((base / "personal-skill").is_symlink())
                self.assertEqual("user-owned", (base / "notes.txt").read_text().strip())

    def test_installer_retargets_links_from_registered_previous_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_tool = make_tool(root / "old")
            new_tool = make_tool(root / "new")
            hub_remote = make_hub_origin(root)
            home = root / "home"
            install_agent_access(
                old_tool,
                hub_remote=str(hub_remote),
                home=home,
            )
            stale_source = old_tool / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                (base / "wuditask").symlink_to(
                    stale_source,
                    target_is_directory=True,
                )

            result = install_agent_access(
                new_tool,
                hub_remote=str(hub_remote),
                home=home,
            )

            self.assertEqual(2, len(result["removed_links"]))
            config = json.loads((home / ".wuditask" / "config.json").read_text())
            self.assertEqual(str(new_tool.resolve()), config["tool_path"])
            for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
                self.assertFalse((base / "wuditask").exists())
                for name in EXPECTED_SKILLS:
                    self.assertEqual(
                        (new_tool / ".agents" / "skills" / name).resolve(),
                        (base / name).resolve(),
                    )
            self.assertEqual(
                (new_tool / "tools" / "wuditask.py").resolve(),
                (home / ".local" / "bin" / "wuditask").resolve(),
            )

    def test_install_conflict_does_not_partially_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            hub_remote = make_hub_origin(root)
            home = root / "home"
            agent_skills = home / ".agents" / "skills"
            agent_skills.mkdir(parents=True)
            stale_source = tool / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            (agent_skills / "wuditask").symlink_to(
                stale_source,
                target_is_directory=True,
            )
            conflict = agent_skills / "wuditask-list"
            conflict.write_text("user-owned\n")

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=home,
                )

            self.assertEqual("install_path_exists", raised.exception.code)
            self.assertTrue((agent_skills / "wuditask").is_symlink())
            self.assertEqual("user-owned", conflict.read_text().strip())
            self.assertFalse((agent_skills / "wuditask-add").exists())
            self.assertFalse((home / ".claude").exists())
            self.assertFalse((home / ".wuditask" / "config.json").exists())

    def test_invalid_product_parent_does_not_partially_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = make_tool(root)
            hub_remote = make_hub_origin(root)
            home = root / "home"
            agent_skills = home / ".agents" / "skills"
            agent_skills.mkdir(parents=True)
            stale_source = tool / ".agents" / "skills" / "wuditask"
            stale_source.mkdir()
            (agent_skills / "wuditask").symlink_to(
                stale_source,
                target_is_directory=True,
            )
            (home / ".claude").write_text("user-owned\n")

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=home,
                )

            self.assertEqual("install_path_exists", raised.exception.code)
            self.assertTrue((agent_skills / "wuditask").is_symlink())
            self.assertFalse((agent_skills / "wuditask-add").exists())
            self.assertEqual("user-owned", (home / ".claude").read_text().strip())
            self.assertFalse((home / ".wuditask" / "config.json").exists())

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tool = make_tool(home / "fixture", content_source=ROOT)
            hub_remote = make_hub_origin(home)
            first = install_agent_access(
                tool,
                hub_remote=str(hub_remote),
                home=home,
            )
            marker = Path(first["hub_cache"]) / "reuse-marker"
            marker.write_text("preserved\n", encoding="utf-8")
            second = install_agent_access(
                tool,
                hub_remote=str(hub_remote),
                home=home,
            )
            self.assertTrue(all(not link["changed"] for link in second["links"]))
            self.assertEqual(first["hub_cache"], second["hub_cache"])
            self.assertEqual("preserved\n", marker.read_text(encoding="utf-8"))

    def test_installer_rejects_a_missing_required_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = "wuditask-release"
            tool = make_tool(root, EXPECTED_SKILLS - {missing})
            hub_remote = make_hub_origin(root)

            with self.assertRaises(WudiTaskError) as raised:
                install_agent_access(
                    tool,
                    hub_remote=str(hub_remote),
                    home=root / "home",
                )

            self.assertEqual("invalid_tool_clone", raised.exception.code)
            self.assertEqual([missing], raised.exception.details["missing_skills"])
            self.assertEqual([], raised.exception.details["unexpected_skills"])


if __name__ == "__main__":
    unittest.main()
