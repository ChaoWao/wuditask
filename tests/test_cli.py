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


if __name__ == "__main__":
    unittest.main()
