from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json

from tests.helpers import add_task, git, make_hub_origin

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "wuditask.py"


class CliTests(unittest.TestCase):
    def run_cli(self, hub: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--hub",
                str(hub),
                "--local",
                "--json",
                "--actor",
                "alice:1001",
                *arguments,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_json_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            added = self.run_cli(
                hub,
                "add",
                "--id",
                "WDT-20260711T120000Z-A1B2C3",
                "--repo",
                "acme/service",
                "--title",
                "CLI task",
                "--goal",
                "Exercise the CLI.",
                "--accept",
                "The lifecycle passes.",
                "--verify",
                "command::python3 -m unittest",
            )
            self.assertEqual(0, added.returncode, added.stderr)
            add_payload = json.loads(added.stdout)
            self.assertTrue(add_payload["ok"])

            claimed = self.run_cli(
                hub,
                "execute",
                "WDT-20260711T120000Z-A1B2C3",
                "--repo",
                "acme/service",
            )
            self.assertEqual(0, claimed.returncode, claimed.stderr)
            claim_payload = json.loads(claimed.stdout)
            self.assertTrue(claim_payload["sync"]["confirmed"])

            archived = self.run_cli(
                hub,
                "archive",
                "WDT-20260711T120000Z-A1B2C3",
                "--result",
                "CLI lifecycle passed.",
                "--evidence",
                "AC-1=unittest passed",
            )
            self.assertEqual(0, archived.returncode, archived.stderr)
            self.assertTrue(json.loads(archived.stdout)["confirmed"])

    def test_local_hub_path_is_explicit_and_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            missing = subprocess.run(
                [sys.executable, str(TOOL), "--local", "--json", "validate"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            remote = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--hub",
                    str(hub),
                    "--json",
                    "validate",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(
            "local_hub_required", json.loads(missing.stdout)["error"]["code"]
        )
        self.assertEqual(
            "remote_hub_path_invalid", json.loads(remote.stdout)["error"]["code"]
        )

    def test_cli_build_site_uses_tool_assets_with_data_only_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hub = base / "hub"
            TaskRepository(hub).initialize()
            output = base / "site"
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--hub",
                    str(hub),
                    "--local",
                    "--json",
                    "build-site",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((hub / "site").exists())

    def test_remote_read_uses_configured_hub_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            hub = make_hub_origin(base, branch="queue")
            seed = base / "hub-seed"
            add_task(
                TaskRepository(seed),
                "WDT-20260711T120007Z-888888",
                title="Configured Hub task",
            )
            git(["add", "data"], seed)
            git(["commit", "-m", "add configured task"], seed)
            git(["push", "origin", "queue"], seed)
            atomic_write_json(
                home / ".wuditask" / "config.json",
                {
                    "schema_version": 2,
                    "tool_path": str(ROOT),
                    "tool_remote": git(
                        ["remote", "get-url", "origin"], ROOT
                    ).stdout.strip(),
                    "tool_branch": git(
                        ["branch", "--show-current"], ROOT
                    ).stdout.strip(),
                    "hub_remote": str(hub),
                    "hub_branch": "queue",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )
            environment = {**os.environ, "HOME": str(home)}

            result = subprocess.run(
                [sys.executable, str(TOOL), "--json", "list"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(1, payload["count"])
        self.assertEqual(
            "WDT-20260711T120007Z-888888",
            payload["open_tasks"][0]["id"],
        )

    def test_missing_spec_returns_structured_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_cli(Path(temporary), "add", "--title", "Incomplete")
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("insufficient_task_spec", payload["error"]["code"])
        self.assertIn("questions", payload["error"]["details"])

    def test_help_is_read_only_and_topic_aware(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--json",
                "help",
                "archive",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("archive", payload["topic"])
        self.assertEqual(["archive"], [item["name"] for item in payload["commands"]])
        self.assertEqual("wuditask help [topic]", payload["cli_invocation"])
        self.assertEqual(
            "$wuditask-archive",
            payload["commands"][0]["agent_usage"]["codex"],
        )

        selfupdate = subprocess.run(
            [sys.executable, str(TOOL), "--json", "help", "selfupdate"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, selfupdate.returncode, selfupdate.stderr)
        selfupdate_payload = json.loads(selfupdate.stdout)
        self.assertEqual(
            "/wuditask-selfupdate fix <request>",
            selfupdate_payload["commands"][0]["agent_usage"]["claude_fix"],
        )
        self.assertEqual(
            {"codex", "claude", "codex_fix", "claude_fix"},
            set(selfupdate_payload["commands"][0]["agent_usage"]),
        )
        self.assertTrue(
            any(
                "does not create an Issue or queue task" in note
                for note in selfupdate_payload["notes"]
            )
        )

        add = subprocess.run(
            [sys.executable, str(TOOL), "--json", "help", "add"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, add.returncode, add.stderr)
        add_payload = json.loads(add.stdout)
        self.assertEqual(
            "$wuditask-add", add_payload["commands"][0]["agent_usage"]["codex"]
        )
        self.assertIn("--link ISSUE_OR_PR_URL", add_payload["commands"][0]["usage"])

    def test_help_routes_every_operation_to_a_dedicated_skill(self) -> None:
        routes = {
            "add": ("codex", "$wuditask-add"),
            "execute": ("codex", "$wuditask-execute"),
            "dep-check": ("codex", "$wuditask-dep-check"),
            "archive": ("codex", "$wuditask-archive"),
            "release": ("codex", "$wuditask-release"),
            "list": ("codex", "$wuditask-list"),
            "show": ("codex", "$wuditask-show"),
            "install": ("codex", "$wuditask-install"),
            "selfupdate": ("codex", "$wuditask-selfupdate"),
        }
        for topic, (key, invocation) in routes.items():
            with self.subTest(topic=topic):
                result = subprocess.run(
                    [sys.executable, str(TOOL), "--json", "help", topic],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(
                    invocation,
                    payload["commands"][0]["agent_usage"][key],
                )

        text_help = subprocess.run(
            [sys.executable, str(TOOL), "help", "selfupdate"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, text_help.returncode, text_help.stderr)
        self.assertIn("$wuditask-selfupdate fix <request>", text_help.stdout)
        self.assertIn("does not create an Issue or queue task", text_help.stdout)

    def test_module_entry_point_uses_the_tool_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "wuditask", "--version"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("wuditask 0.3.0", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
