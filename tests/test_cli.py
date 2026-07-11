from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(
            "/wuditask help [topic]", payload["agent_invocation"]["claude"]
        )
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
            "/wuditask-selfupdate fix <request>",
            selfupdate_payload["commands"][0]["agent_usage"]["fix"],
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
            "list": ("codex", "$wuditask-inspect"),
            "show": ("codex", "$wuditask-inspect"),
            "install": ("codex", "$wuditask-install"),
            "selfupdate": ("codex_update", "$wuditask-selfupdate"),
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


if __name__ == "__main__":
    unittest.main()
