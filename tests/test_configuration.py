from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wuditask.configuration import load_config
from wuditask.errors import WudiTaskError
from wuditask.util import atomic_write_json


class ConfigurationTests(unittest.TestCase):
    def test_loads_strict_two_repository_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tool = home / "tool"
            tool.mkdir()
            atomic_write_json(
                home / ".wuditask" / "config.json",
                {
                    "schema_version": 2,
                    "tool_path": str(tool),
                    "tool_remote": "https://example.test/wuditask.git",
                    "tool_branch": "main",
                    "hub_remote": "https://example.test/wuditask-hub.git",
                    "hub_branch": "queue",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )

            config = load_config(home=home, expected_tool_path=tool)

        self.assertEqual(tool.resolve(), config.tool_path)
        self.assertEqual("queue", config.hub_branch)

    def test_rejects_legacy_hub_path_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            atomic_write_json(
                home / ".wuditask" / "config.json",
                {
                    "schema_version": 1,
                    "hub_path": str(home / "legacy"),
                    "remote": "https://example.test/wuditask.git",
                    "branch": "main",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )

            with self.assertRaises(WudiTaskError) as raised:
                load_config(home=home)

        self.assertEqual("wuditask_config_invalid", raised.exception.code)
        self.assertIn("legacy hub_path", raised.exception.details["action"])

    def test_rejects_invocation_from_an_unregistered_tool_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            registered = home / "registered"
            invoked = home / "invoked"
            registered.mkdir()
            invoked.mkdir()
            atomic_write_json(
                home / ".wuditask" / "config.json",
                {
                    "schema_version": 2,
                    "tool_path": str(registered),
                    "tool_remote": "https://example.test/wuditask.git",
                    "tool_branch": "main",
                    "hub_remote": "https://example.test/wuditask-hub.git",
                    "hub_branch": "main",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )

            with self.assertRaises(WudiTaskError) as raised:
                load_config(home=home, expected_tool_path=invoked)

        self.assertEqual("wuditask_tool_registration_mismatch", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
