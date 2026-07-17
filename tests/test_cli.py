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
    @staticmethod
    def fake_github(
        base: Path, *, assignees: list[str] | None = None
    ) -> tuple[dict[str, str], Path, Path]:
        fake_bin = base / "fake-bin"
        fake_bin.mkdir()
        state = base / "github-state.json"
        log = base / "github-calls.log"
        atomic_write_json(state, {"assignees": assignees or []})
        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "state_path = pathlib.Path(os.environ['FAKE_GH_STATE'])\n"
            "log_path = pathlib.Path(os.environ['FAKE_GH_LOG'])\n"
            "state = json.loads(state_path.read_text())\n"
            "if args[:2] == ['api', 'user']:\n"
            "    print(json.dumps({'login': 'alice', 'id': 1001}))\n"
            "elif args[:2] == ['issue', 'view']:\n"
            "    if os.environ.get('FAKE_GH_FAIL_VIEW') == '1':\n"
            "        print('not found', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    print(json.dumps({'state': 'OPEN', 'stateReason': None, "
            "'url': 'https://github.com/acme/service/issues/42', "
            "'assignees': [{'login': x} for x in state['assignees']], "
            "'closedByPullRequestsReferences': [], "
            "'updatedAt': '2026-07-16T10:00:00Z'}))\n"
            "elif args[:2] == ['issue', 'edit']:\n"
            "    if '--add-assignee' in args:\n"
            "        login = args[args.index('--add-assignee') + 1]\n"
            "        if login not in state['assignees']: state['assignees'].append(login)\n"
            "    elif '--remove-assignee' in args:\n"
            "        login = args[args.index('--remove-assignee') + 1]\n"
            "        state['assignees'] = [x for x in state['assignees'] if x != login]\n"
            "    state_path.write_text(json.dumps(state))\n"
            "    with log_path.open('a') as handle: handle.write(' '.join(args) + '\\n')\n"
            "else:\n"
            "    print('unsupported fake gh command', file=sys.stderr)\n"
            "    raise SystemExit(1)\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STATE": str(state),
            "FAKE_GH_LOG": str(log),
        }
        return environment, state, log

    def run_cli(
        self,
        hub: Path,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
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
            env=environment,
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
                "--text-source-reason",
                "CLI lifecycle fixture has no external narrative.",
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

            deleted = self.run_cli(
                hub,
                "delete",
                "WDT-20260711T120000Z-A1B2C3",
                "--reason",
                "The lifecycle fixture is not real work.",
            )
            self.assertNotEqual(0, deleted.returncode)
            delete_payload = json.loads(deleted.stdout)
            self.assertFalse(delete_payload["ok"])
            self.assertEqual(
                "delete_remote_hub_required",
                delete_payload["error"]["code"],
            )
            self.assertIn(
                "WDT-20260711T120000Z-A1B2C3",
                TaskRepository(hub).load_index().archived,
            )
            self.assertEqual({}, TaskRepository(hub).load_deletion_receipts())

    def test_add_parses_hub_fallback_source_separately_from_execution_repo(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            git(["init", "-b", "main"], hub)
            git(
                [
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/acme/wuditask-hub.git",
                ],
                hub,
            )
            environment, _, _ = self.fake_github(hub)
            added = self.run_cli(
                hub,
                "add",
                "--id",
                "WDT-20260711T120000Z-A1B2C3",
                "--repo",
                "acme/service",
                "--source",
                "https://github.com/acme/wuditask-hub/issues/42",
                "--source-fallback-reason",
                "The execution repository has Issues disabled.",
                "--title",
                "Fallback task",
                "--goal",
                "Exercise structured fallback source parsing.",
                "--accept",
                "The task is recorded.",
                environment=environment,
            )

        self.assertEqual(0, added.returncode, added.stdout)
        source = json.loads(added.stdout)["task"]["source"]
        self.assertEqual(
            {
                "kind": "github_issue_fallback",
                "repo": "acme/wuditask-hub",
                "number": 42,
                "fallback_reason": "The execution repository has Issues disabled.",
            },
            source,
        )

    def test_malformed_source_spec_returns_structured_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            spec_path = hub / "malformed.json"
            atomic_write_json(
                spec_path,
                {
                    "title": "Malformed source",
                    "repo": "acme/service",
                    "source": {"kind": "github_issue", "number": 42},
                    "goal": "Return a structured validation error.",
                    "acceptance_criteria": ["The error is structured."],
                },
            )
            result = self.run_cli(hub, "add", "--spec", str(spec_path))

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(
            "invalid_task_data", json.loads(result.stdout)["error"]["code"]
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_add_rejects_wrong_or_unreadable_github_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            git(["init", "-b", "main"], hub)
            git(
                [
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/acme/wuditask-hub.git",
                ],
                hub,
            )
            environment, _, _ = self.fake_github(hub)
            common = (
                "--id",
                "WDT-20260711T120000Z-A1B2C3",
                "--repo",
                "acme/service",
                "--title",
                "Canonical source validation",
                "--goal",
                "Reject invalid canonical sources.",
                "--accept",
                "The invalid source is rejected.",
            )
            wrong_hub = self.run_cli(
                hub,
                "add",
                *common,
                "--source",
                "https://github.com/acme/other-hub/issues/42",
                "--source-fallback-reason",
                "The execution repository cannot host Issues.",
                environment=environment,
            )
            unavailable_environment = {**environment, "FAKE_GH_FAIL_VIEW": "1"}
            unavailable = self.run_cli(
                hub,
                "add",
                *common,
                "--source",
                "https://github.com/acme/service/issues/404",
                environment=unavailable_environment,
            )

        self.assertEqual(
            "invalid_fallback_repository",
            json.loads(wrong_hub.stdout)["error"]["code"],
        )
        self.assertEqual(
            "github_source_unavailable",
            json.loads(unavailable.stdout)["error"]["code"],
        )

    def test_local_execute_never_mutates_real_github_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            task = add_task(
                repository,
                "WDT-20260711T120000Z-A1B2C3",
            )
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(
                repository.open_dir / "WDT-20260711T120000Z-A1B2C3.json", task
            )
            environment, _, log = self.fake_github(hub)
            result = self.run_cli(
                hub,
                "execute",
                "WDT-20260711T120000Z-A1B2C3",
                "--repo",
                "acme/service",
                environment=environment,
            )

            self.assertEqual(3, result.returncode, result.stdout)
            self.assertEqual(
                "github_claim_reconciliation_failed",
                json.loads(result.stdout)["error"]["code"],
            )
            self.assertFalse(log.exists())
            self.assertIsNone(
                repository.load_index()
                .open["WDT-20260711T120000Z-A1B2C3"]
                .task["claim"]
            )

    def test_remote_execute_and_release_sync_github_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            hub = make_hub_origin(base)
            seed = base / "hub-seed"
            repository = TaskRepository(seed)
            task = add_task(
                repository,
                "WDT-20260711T120000Z-A1B2C3",
            )
            task["source"] = {
                "kind": "github_issue",
                "repo": "acme/service",
                "number": 42,
            }
            atomic_write_json(
                repository.open_dir / "WDT-20260711T120000Z-A1B2C3.json", task
            )
            git(["add", "data"], seed)
            git(["commit", "-m", "add GitHub-backed task"], seed)
            git(["push", "origin", "main"], seed)
            environment, state, log = self.fake_github(base)
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
                    "hub_branch": "main",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )
            environment["HOME"] = str(home)

            claimed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--json",
                    "execute",
                    "WDT-20260711T120000Z-A1B2C3",
                    "--repo",
                    "acme/service",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            released = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--json",
                    "release",
                    "WDT-20260711T120000Z-A1B2C3",
                    "--reason",
                    "Return to the shared queue.",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, claimed.returncode, claimed.stdout)
            self.assertTrue(json.loads(claimed.stdout)["work_authorized"])
            self.assertEqual(0, released.returncode, released.stdout)
            self.assertTrue(json.loads(released.stdout)["confirmed"])
            self.assertEqual([], json.loads(state.read_text())["assignees"])
            calls = log.read_text(encoding="utf-8")
            self.assertIn("--add-assignee alice", calls)
            self.assertIn("--remove-assignee alice", calls)
            git(["pull", "--ff-only"], seed)
            self.assertIsNone(
                TaskRepository(seed)
                .load_index()
                .open["WDT-20260711T120000Z-A1B2C3"]
                .task["claim"]
            )

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

    def test_remote_cache_io_failure_preserves_the_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            hub = make_hub_origin(base)
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
                    "hub_branch": "main",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )
            invalid_xdg = base / "cache-file"
            invalid_xdg.write_text("not a directory\n", encoding="utf-8")
            environment = {
                **os.environ,
                "HOME": str(home),
                "XDG_CACHE_HOME": str(invalid_xdg),
            }

            result = subprocess.run(
                [sys.executable, str(TOOL), "--json", "validate"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(4, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("hub_cache_io_failed", payload["error"]["code"])

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
        self.assertIn("--source ISSUE_OR_PR_URL", add_payload["commands"][0]["usage"])

    def test_help_routes_every_operation_to_a_dedicated_skill(self) -> None:
        routes = {
            "add": ("codex", "$wuditask-add"),
            "execute": ("codex", "$wuditask-execute"),
            "dep-check": ("codex", "$wuditask-dep-check"),
            "archive": ("codex", "$wuditask-archive"),
            "delete": ("codex", "$wuditask-delete"),
            "release": ("codex", "$wuditask-release"),
            "list": ("codex", "$wuditask-list"),
            "show": ("codex", "$wuditask-show"),
            "reconcile": ("codex", "$wuditask-reconcile"),
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
        self.assertEqual("wuditask 0.5.0", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
