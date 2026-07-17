from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json

from tests.helpers import RUN_ID, add_task, git, make_hub_origin

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "wuditask.py"
TASK_A = "WDT-20260711T120000Z-A1B2C3"
TASK_B = "WDT-20260711T120001Z-B2C3D4"
TASK_C = "WDT-20260711T120002Z-C3D4E5"
RUN_ID_RE = re.compile(r"^WDX-[0-9A-F]{24}$")


class FakeGitHub:
    """Small stateful gh replacement covering the CLI's Issue/PR contract."""

    def __init__(self, base: Path, *, login: str = "alice") -> None:
        fake_bin = base / "fake-bin"
        fake_bin.mkdir()
        self.state_path = base / "github-state.json"
        self.log_path = base / "github-calls.log"
        atomic_write_json(
            self.state_path,
            {"login": login, "issues": {}, "pull_requests": {}},
        )
        executable = fake_bin / "gh"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "state_path = pathlib.Path(os.environ['FAKE_GH_STATE'])\n"
            "log_path = pathlib.Path(os.environ['FAKE_GH_LOG'])\n"
            "state = json.loads(state_path.read_text())\n"
            "with log_path.open('a') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['api', 'user']:\n"
            "    print(json.dumps({'login': state['login']}))\n"
            "    raise SystemExit(0)\n"
            "if os.environ.get('FAKE_GH_FAIL_VIEW') == '1' and len(args) > 1 and args[1] == 'view':\n"
            "    print('not found', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "if len(args) < 3 or args[0] not in {'issue', 'pr'}:\n"
            "    print('unsupported fake gh command: ' + ' '.join(args), file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "kind, action, number = args[0], args[1], args[2]\n"
            "repo = args[args.index('--repo') + 1]\n"
            "bucket = state['issues'] if kind == 'issue' else state['pull_requests']\n"
            "item = bucket.get(number)\n"
            "if item is None:\n"
            "    print('not found', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "if action == 'view' and kind == 'issue':\n"
            "    payload = dict(item)\n"
            "    payload['url'] = f'https://github.com/{repo}/issues/{number}'\n"
            "    payload['assignees'] = [{'login': login} for login in item['assignees']]\n"
            "    payload['closedByPullRequestsReferences'] = [\n"
            "        {'number': pr, 'url': f'https://github.com/{repo}/pull/{pr}',\n"
            "         'repository': {'nameWithOwner': repo}} for pr in item.get('prs', [])\n"
            "    ]\n"
            "    payload.pop('prs', None)\n"
            "    print(json.dumps(payload))\n"
            "    raise SystemExit(0)\n"
            "if action == 'view' and kind == 'pr':\n"
            "    payload = dict(item)\n"
            "    author = item.get('author')\n"
            "    payload['author'] = {'login': author} if author else None\n"
            "    payload['assignees'] = [{'login': login} for login in item['assignees']]\n"
            "    payload['statusCheckRollup'] = item.get('checks', [])\n"
            "    payload.pop('checks', None)\n"
            "    print(json.dumps(payload))\n"
            "    raise SystemExit(0)\n"
            "if action == 'edit':\n"
            "    if '--add-assignee' in args:\n"
            "        login = args[args.index('--add-assignee') + 1]\n"
            "        if login not in item['assignees']:\n"
            "            item['assignees'].append(login)\n"
            "    elif '--remove-assignee' in args:\n"
            "        login = args[args.index('--remove-assignee') + 1]\n"
            "        item['assignees'] = [value for value in item['assignees'] if value != login]\n"
            "    else:\n"
            "        print('unsupported edit', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    state_path.write_text(json.dumps(state))\n"
            "    raise SystemExit(0)\n"
            "print('unsupported fake gh command: ' + ' '.join(args), file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state_path),
            "FAKE_GH_LOG": str(self.log_path),
        }

    def _state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def issue(
        self,
        number: int,
        *,
        assignees: list[str] | None = None,
        prs: list[int] | None = None,
        state: str = "OPEN",
        state_reason: str | None = None,
        title: str | None = None,
        body: str = "Goal and acceptance live here.",
    ) -> None:
        payload = self._state()
        payload["issues"][str(number)] = {
            "title": title or f"Issue {number}",
            "body": body,
            "state": state,
            "stateReason": state_reason,
            "assignees": assignees or [],
            "prs": prs or [],
            "updatedAt": "2026-07-16T10:00:00Z",
        }
        atomic_write_json(self.state_path, payload)

    def pull_request(
        self,
        number: int,
        *,
        author: str = "carol",
        assignees: list[str] | None = None,
        state: str = "OPEN",
        draft: bool = False,
        merged_at: str | None = None,
        review_decision: str | None = "REVIEW_REQUIRED",
        merge_state: str = "BLOCKED",
        checks: list[dict[str, str]] | None = None,
        title: str | None = None,
    ) -> None:
        payload = self._state()
        payload["pull_requests"][str(number)] = {
            "title": title or f"PR {number}",
            "body": "Implementation and acceptance evidence.",
            "author": author,
            "assignees": assignees or [],
            "state": state,
            "isDraft": draft,
            "mergedAt": merged_at,
            "reviewDecision": review_decision,
            "mergeStateStatus": merge_state,
            "checks": checks or [],
            "updatedAt": "2026-07-16T11:00:00Z",
        }
        atomic_write_json(self.state_path, payload)

    def assignees(self, kind: str, number: int) -> list[str]:
        bucket = "issues" if kind == "issue" else "pull_requests"
        return self._state()[bucket][str(number)]["assignees"]

    def calls(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]


class CliTests(unittest.TestCase):
    def run_local(
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
                "alice",
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def run_remote(
        self,
        environment: dict[str, str],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), "--json", *arguments],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def seed_task(
        repository: TaskRepository,
        task_id: str,
        *,
        number: int,
        priority: str = "P2",
        source_kind: str = "github_issue",
        active_agents: list[dict[str, str]] | None = None,
        dependencies: list[str] | None = None,
    ) -> dict[str, Any]:
        task = add_task(
            repository,
            task_id,
            number=number,
            dependencies=dependencies,
        )
        task["priority"] = priority
        task["source"]["kind"] = source_kind
        task["active_agents"] = active_agents or []
        atomic_write_json(repository.open_dir / f"{task_id}.json", task)
        return task

    @staticmethod
    def configure_remote(base: Path, github: FakeGitHub) -> tuple[Path, Path, dict[str, str]]:
        home = base / "home"
        home.mkdir()
        origin = make_hub_origin(base)
        tool_remote = git(["remote", "get-url", "origin"], ROOT).stdout.strip()
        tool_branch = git(["branch", "--show-current"], ROOT).stdout.strip()
        atomic_write_json(
            home / ".wuditask" / "config.json",
            {
                "schema_version": 2,
                "tool_path": str(ROOT),
                "tool_remote": tool_remote,
                "tool_branch": tool_branch,
                "hub_remote": str(origin),
                "hub_branch": "main",
                "installed_at": "2026-07-11T12:00:00Z",
            },
        )
        environment = {
            **github.environment,
            "HOME": str(home),
            "XDG_CACHE_HOME": str(base / "cache"),
        }
        return origin, base / "hub-seed", environment

    @staticmethod
    def publish_seed(seed: Path, message: str = "seed tasks") -> None:
        git(["add", "data"], seed)
        git(["commit", "-m", message], seed)
        git(["push", "origin", "main"], seed)

    def test_add_accepts_only_canonical_github_issue_or_pr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            github = FakeGitHub(hub)
            github.issue(42)
            github.pull_request(43)

            issue = self.run_local(
                hub,
                "add",
                "--id",
                TASK_A,
                "--repo",
                "acme/service",
                "--source",
                "https://github.com/acme/service/issues/42",
                environment=github.environment,
            )
            pull = self.run_local(
                hub,
                "add",
                "--id",
                TASK_B,
                "--repo",
                "acme/service",
                "--source",
                "https://github.com/acme/service/pull/43",
                environment=github.environment,
            )

            self.assertEqual(0, issue.returncode, issue.stdout + issue.stderr)
            self.assertEqual(0, pull.returncode, pull.stdout + pull.stderr)
            issue_task = json.loads(issue.stdout)["task"]
            pull_task = json.loads(pull.stdout)["task"]
            self.assertEqual("github_issue", issue_task["source"]["kind"])
            self.assertEqual("github_pull_request", pull_task["source"]["kind"])
            self.assertEqual("alice", issue_task["created_by"])
            self.assertEqual(
                {
                    "schema_version",
                    "id",
                    "repo",
                    "source",
                    "created_by",
                    "priority",
                    "created_at",
                    "dependencies",
                    "active_agents",
                },
                set(issue_task),
            )

            text = self.run_local(
                hub,
                "add",
                "--repo",
                "acme/service",
                "--text-source-reason",
                "No source",
                environment=github.environment,
            )

        self.assertEqual(2, text.returncode)
        self.assertIn("unrecognized arguments", text.stderr)

    def test_add_rejects_unreadable_source_without_creating_text_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            github = FakeGitHub(hub)
            environment = {**github.environment, "FAKE_GH_FAIL_VIEW": "1"}
            result = self.run_local(
                hub,
                "add",
                "--id",
                TASK_A,
                "--repo",
                "acme/service",
                "--source",
                "https://github.com/acme/service/issues/404",
                environment=environment,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(
                "github_source_unavailable",
                json.loads(result.stdout)["error"]["code"],
            )
            self.assertEqual({}, TaskRepository(hub).load_index().all)

    def test_assign_and_unassign_change_only_github_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            self.seed_task(repository, TASK_A, number=12)
            github = FakeGitHub(hub)
            github.issue(12)
            before = (repository.open_dir / f"{TASK_A}.json").read_bytes()

            assigned = self.run_local(
                hub,
                "assign",
                TASK_A,
                "--to",
                "bob",
                environment=github.environment,
            )
            after_assign = (repository.open_dir / f"{TASK_A}.json").read_bytes()
            unassigned = self.run_local(
                hub,
                "unassign",
                TASK_A,
                "--from",
                "bob",
                environment=github.environment,
            )

            self.assertEqual(0, assigned.returncode, assigned.stdout + assigned.stderr)
            self.assertEqual(["bob"], json.loads(assigned.stdout)["delivery"]["owners"])
            self.assertEqual(0, unassigned.returncode, unassigned.stdout + unassigned.stderr)
            self.assertEqual([], github.assignees("issue", 12))
            self.assertEqual(before, after_assign)
            self.assertEqual(
                before,
                (repository.open_dir / f"{TASK_A}.json").read_bytes(),
            )

    def test_assign_supports_pull_request_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            self.seed_task(
                repository,
                TASK_A,
                number=22,
                source_kind="github_pull_request",
            )
            github = FakeGitHub(hub)
            github.pull_request(22, author="carol")

            result = self.run_local(
                hub,
                "assign",
                TASK_A,
                "--to",
                "bob",
                environment=github.environment,
            )
            assignees = github.assignees("pr", 22)
            calls = github.calls()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["bob"], assignees)
        self.assertTrue(any(call[:2] == ["pr", "edit"] for call in calls))

    def test_unassign_refuses_login_with_active_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            self.seed_task(
                repository,
                TASK_A,
                number=12,
                active_agents=[{"login": "bob", "run_id": RUN_ID}],
            )
            github = FakeGitHub(hub)
            github.issue(12, assignees=["bob"])

            result = self.run_local(
                hub,
                "unassign",
                TASK_A,
                "--from",
                "bob",
                environment=github.environment,
            )
            assignees = github.assignees("issue", 12)
            calls = github.calls()

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "active_agent_prevents_unassign",
            json.loads(result.stdout)["error"]["code"],
        )
        self.assertEqual(["bob"], assignees)
        self.assertFalse(any("--remove-assignee" in call for call in calls))

    def test_assignment_commands_reject_archived_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            task = self.seed_task(repository, TASK_A, number=12)
            task["completion"] = {
                "outcome": "done",
                "completed_at": "2026-07-16T12:00:00Z",
                "completed_by": "alice",
                "result": "Verified.",
                "evidence": ["Tests passed."],
                "participants": [{"login": "alice", "run_id": RUN_ID}],
            }
            repository.archive(task)
            github = FakeGitHub(hub)
            github.issue(12, assignees=["alice"])

            assigned = self.run_local(
                hub,
                "assign",
                TASK_A,
                "--to",
                "bob",
                environment=github.environment,
            )
            unassigned = self.run_local(
                hub,
                "unassign",
                TASK_A,
                "--from",
                "alice",
                environment=github.environment,
            )

        self.assertEqual(
            "task_already_archived",
            json.loads(assigned.stdout)["error"]["code"],
        )
        self.assertEqual(
            "task_already_archived",
            json.loads(unassigned.stdout)["error"]["code"],
        )
        self.assertFalse(any(call[1] == "edit" for call in github.calls()))

    def test_local_execute_is_rejected_before_assignment_or_hub_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            repository = TaskRepository(hub)
            repository.initialize()
            self.seed_task(repository, TASK_A, number=12)
            github = FakeGitHub(hub)
            github.issue(12)

            result = self.run_local(
                hub,
                "execute",
                TASK_A,
                "--repo",
                "acme/service",
                environment=github.environment,
            )
            active = repository.load_index().open[TASK_A].task["active_agents"]
            calls = github.calls()

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "execute_remote_hub_required",
            json.loads(result.stdout)["error"]["code"],
        )
        self.assertEqual([], active)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in calls))

    def test_remote_execute_prefers_assigned_idle_task_over_unowned_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12, assignees=["alice"])
            github.issue(13)
            github.issue(14, assignees=["bob"])
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(repository, TASK_A, number=12, priority="P3")
            self.seed_task(repository, TASK_B, number=13, priority="P0")
            self.seed_task(repository, TASK_C, number=14, priority="P0")
            self.publish_seed(seed)

            result = self.run_remote(
                environment,
                "execute",
                "--repo",
                "acme/service",
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(TASK_A, payload["task_id"])
            self.assertRegex(payload["run_id"], RUN_ID_RE)
            self.assertTrue(payload["confirmed"])
            self.assertTrue(payload["sync"]["confirmed"])
            self.assertTrue(payload["work_authorized"])
            self.assertFalse(
                any("--add-assignee" in call for call in github.calls())
            )
            git(["pull", "--ff-only"], seed)
            active = TaskRepository(seed).load_index().open[TASK_A].task["active_agents"]
            self.assertEqual(
                [{"login": "alice", "run_id": payload["run_id"]}],
                active,
            )

    def test_remote_execute_self_assigns_unowned_before_starting_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(13)
            github.issue(14, assignees=["bob"])
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(repository, TASK_B, number=13, priority="P2")
            self.seed_task(repository, TASK_C, number=14, priority="P0")
            self.publish_seed(seed)

            result = self.run_remote(
                environment,
                "execute",
                "--repo",
                "acme/service",
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(TASK_B, payload["task_id"])
            self.assertEqual(["alice"], github.assignees("issue", 13))
            self.assertTrue(payload["github_assignment"]["changed"])
            self.assertTrue(payload["github_assignment"]["confirmed"])
            self.assertTrue(payload["work_authorized"])
            git(["pull", "--ff-only"], seed)
            active = TaskRepository(seed).load_index().open[TASK_B].task["active_agents"]
            self.assertEqual(payload["run_id"], active[0]["run_id"])

    def test_explicit_execute_self_assigns_alongside_existing_owners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12, assignees=["bob"])
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(repository, TASK_A, number=12)
            self.publish_seed(seed)

            result = self.run_remote(
                environment,
                "execute",
                TASK_A,
                "--repo",
                "acme/service",
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(TASK_A, payload["task_id"])
            self.assertEqual(["bob", "alice"], github.assignees("issue", 12))
            self.assertTrue(payload["github_assignment"]["changed"])
            self.assertTrue(payload["work_authorized"])
            git(["pull", "--ff-only"], seed)
            self.assertEqual(
                [{"login": "alice", "run_id": payload["run_id"]}],
                TaskRepository(seed).load_index().open[TASK_A].task["active_agents"],
            )

    def test_explicit_execute_rejects_a_second_run_for_same_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12, assignees=["alice"])
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(
                repository,
                TASK_A,
                number=12,
                active_agents=[{"login": "alice", "run_id": RUN_ID}],
            )
            self.publish_seed(seed)

            result = self.run_remote(
                environment,
                "execute",
                TASK_A,
                "--repo",
                "acme/service",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("active_agent_conflict", json.loads(result.stdout)["error"]["code"])
            self.assertEqual(["alice"], github.assignees("issue", 12))
            self.assertFalse(
                any("--remove-assignee" in call for call in github.calls())
            )
            self.assertEqual(
                [{"login": "alice", "run_id": RUN_ID}],
                TaskRepository(seed).load_index().open[TASK_A].task["active_agents"],
            )

    def test_release_removes_exact_run_without_unassigning_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12, assignees=["alice"])
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(repository, TASK_A, number=12)
            self.publish_seed(seed)
            started = self.run_remote(
                environment,
                "execute",
                TASK_A,
                "--repo",
                "acme/service",
            )
            self.assertEqual(0, started.returncode, started.stdout + started.stderr)
            run_id = json.loads(started.stdout)["run_id"]

            released = self.run_remote(
                environment,
                "release",
                TASK_A,
                "--run-id",
                run_id,
                "--reason",
                "Waiting for input.",
            )

            self.assertEqual(0, released.returncode, released.stdout + released.stderr)
            self.assertEqual(run_id, json.loads(released.stdout)["run_id"])
            self.assertEqual(["alice"], github.assignees("issue", 12))
            self.assertFalse(
                any("--remove-assignee" in call for call in github.calls())
            )
            git(["pull", "--ff-only"], seed)
            self.assertEqual(
                [],
                TaskRepository(seed).load_index().open[TASK_A].task["active_agents"],
            )

    def test_archive_parser_allows_conditionally_optional_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary)
            TaskRepository(hub).initialize()
            terminal_without_run = self.run_local(
                hub,
                "archive",
                TASK_A,
                "--outcome",
                "cancelled",
                "--result",
                "No longer planned.",
            )
            parsed = self.run_local(
                hub,
                "archive",
                TASK_A,
                "--run-id",
                RUN_ID,
                "--result",
                "Verified.",
                "--evidence",
                "python3 -m unittest: 12 passed",
            )

        self.assertEqual("", terminal_without_run.stderr)
        self.assertEqual(
            "archive_remote_hub_required",
            json.loads(terminal_without_run.stdout)["error"]["code"],
        )
        self.assertEqual("", parsed.stderr)
        self.assertEqual(
            "archive_remote_hub_required",
            json.loads(parsed.stdout)["error"]["code"],
        )

    def test_remote_unclaimed_cancelled_archive_needs_no_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12, state="CLOSED", state_reason="NOT_PLANNED")
            _, seed, environment = self.configure_remote(base, github)
            repository = TaskRepository(seed)
            self.seed_task(repository, TASK_B, number=13)
            self.seed_task(
                repository,
                TASK_A,
                number=12,
                dependencies=[TASK_B],
            )
            self.publish_seed(seed)

            archived = self.run_remote(
                environment,
                "archive",
                TASK_A,
                "--outcome",
                "cancelled",
                "--result",
                "No longer planned.",
            )

            self.assertEqual(0, archived.returncode, archived.stdout + archived.stderr)
            payload = json.loads(archived.stdout)
            self.assertIsNone(payload["run_id"])
            self.assertTrue(payload["sync"]["confirmed"])
            git(["pull", "--ff-only"], seed)
            task = TaskRepository(seed).load_index().archived[TASK_A].task
            self.assertEqual([], task["completion"]["participants"])

    def test_old_dep_check_and_reconcile_parsers_do_not_exist(self) -> None:
        for command in ("dep-check", "reconcile"):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(TOOL), "--json", command],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(2, result.returncode)
                self.assertIn("invalid choice", result.stderr)

    def test_local_hub_path_and_remote_cache_errors_keep_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hub = base / "hub"
            TaskRepository(hub).initialize()
            missing = subprocess.run(
                [sys.executable, str(TOOL), "--local", "--json", "validate"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            explicit_remote = subprocess.run(
                [sys.executable, str(TOOL), "--hub", str(hub), "--json", "validate"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual("local_hub_required", json.loads(missing.stdout)["error"]["code"])
        self.assertEqual(
            "remote_hub_path_invalid",
            json.loads(explicit_remote.stdout)["error"]["code"],
        )

    def test_remote_read_uses_configured_hub_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            github.issue(12)
            home = base / "home"
            home.mkdir()
            origin = make_hub_origin(base, branch="queue")
            seed = base / "hub-seed"
            self.seed_task(TaskRepository(seed), TASK_A, number=12)
            git(["add", "data"], seed)
            git(["commit", "-m", "add configured branch task"], seed)
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
                    "hub_remote": str(origin),
                    "hub_branch": "queue",
                    "installed_at": "2026-07-11T12:00:00Z",
                },
            )
            environment = {
                **github.environment,
                "HOME": str(home),
                "XDG_CACHE_HOME": str(base / "cache"),
            }

            result = self.run_remote(environment, "list")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(1, payload["count"])
        self.assertEqual(TASK_A, payload["open_tasks"][0]["id"])

    def test_remote_cache_io_failure_is_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            github = FakeGitHub(base)
            _, _, environment = self.configure_remote(base, github)
            invalid_cache = base / "cache-file"
            invalid_cache.write_text("not a directory\n", encoding="utf-8")
            environment["XDG_CACHE_HOME"] = str(invalid_cache)

            result = self.run_remote(environment, "validate")

        self.assertEqual(4, result.returncode)
        self.assertEqual("", result.stderr)
        self.assertEqual(
            "hub_cache_io_failed",
            json.loads(result.stdout)["error"]["code"],
        )

    def test_build_site_help_and_module_entry_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hub = base / "hub"
            TaskRepository(hub).initialize()
            output = base / "site"
            built = self.run_local(hub, "build-site", "--output", str(output))

            self.assertEqual(0, built.returncode, built.stdout + built.stderr)
            for name in ("index.html", "workflow.html", "install.html", "dag.html"):
                self.assertTrue((output / name).is_file(), name)

        routes = {
            "add": "$wuditask-add",
            "assign": "$wuditask-assign",
            "check": "$wuditask-check",
            "execute": "$wuditask-execute",
            "archive": "$wuditask-archive",
            "release": "$wuditask-release",
            "unassign": "$wuditask-unassign",
        }
        for topic, invocation in routes.items():
            with self.subTest(topic=topic):
                result = subprocess.run(
                    [sys.executable, str(TOOL), "--json", "help", topic],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(invocation, payload["commands"][0]["agent_usage"]["codex"])

        version = subprocess.run(
            [sys.executable, "-m", "wuditask", "--version"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, version.returncode, version.stderr)
        self.assertEqual("wuditask 0.7.0", version.stdout.strip())


if __name__ == "__main__":
    unittest.main()
