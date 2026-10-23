from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask.coordination import ExecutionRepository
from wuditask.errors import WudiTaskError
from wuditask.gitops import GitCoordinator
from wuditask.model import Identity
from wuditask.repository import TaskRepository
from wuditask.util import new_run_id
from tests.helpers import ACTOR, add_task, git, make_repository


def ticket(*, actor="12", task="I_TASK", environment="machine-a"):
    return dict(
        actor_id=actor,
        login="alice",
        task_id=task,
        url="https://github.com/acme/service/issues/12",
        environment=environment,
        session="a" * 32,
        run_id=new_run_id(),
    )


def receipt(run, *, recovery=False):
    result = {
        key: run[key]
        for key in ("actor_id", "task_id", "run_id", "environment", "session")
    }
    result.update(
        operation_id="stop-1",
        method="manual-verification",
        writers_stopped=True,
        pending_operations_reconciled=True,
        verified_by="alice",
        checkpoint="https://github.com/acme/service/issues/12#issuecomment-1",
        evidence="Host session and children stopped; no pending external operations.",
    )
    if recovery:
        result["user_authorization"] = (
            "User confirmed old host isolated and authorized recovery."
        )
    return result


class PersonalCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = ExecutionRepository(self.base)
        self.repo.initialize(
            {"actor_id": "12", "login": "alice"}, "New empty storage, no old writers."
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_same_node_alias_and_login_rename_conflict(self):
        first = ticket()
        self.repo.acquire(first)
        second = ticket()
        second.update(
            login="renamed", url="https://github.com/other/transferred/issues/99"
        )
        with self.assertRaisesRegex(WudiTaskError, "already has an execution"):
            self.repo.acquire(second)
        self.assertFalse(self.repo.acquire({**first, "login": "renamed"})["changed"])

    def test_users_and_tasks_are_independent(self):
        first, second, third = ticket(), ticket(actor="13"), ticket(task="I_OTHER")
        for t in (first, second, third):
            self.repo.acquire(t)
        self.repo.quiesce(first)
        self.repo.release(first, receipt(first))
        self.repo.assert_current(second)
        self.repo.assert_current(third)

    def test_stale_run_and_stop_receipt_cannot_retire_successor(self):
        old = ticket()
        self.repo.acquire(old)
        self.repo.quiesce(old)
        proof = receipt(old)
        self.repo.release(old, proof)
        new = ticket(environment="machine-b")
        self.repo.acquire(new)
        # A response-lost replay is a no-op even after the successor starts.
        self.assertFalse(self.repo.release(old, proof)["changed"])
        with self.assertRaises(WudiTaskError):
            self.repo.release(old, {**proof, "operation_id": "new-operation"})
        with self.assertRaises(WudiTaskError):
            self.repo.acquire(old)
        self.repo.assert_current(new)

    def test_requires_exact_environment_quiesce_and_stop_evidence(self):
        t = ticket()
        self.repo.acquire(t)
        with self.assertRaises(WudiTaskError):
            self.repo.acquire({**t, "environment": "machine-b"})
        with self.assertRaises(WudiTaskError):
            self.repo.release(t, receipt(t))
        self.repo.quiesce(t)
        with self.assertRaises(WudiTaskError):
            self.repo.assert_current(t)
        for field in ("writers_stopped", "pending_operations_reconciled"):
            with self.assertRaises(WudiTaskError):
                self.repo.release(t, {**receipt(t), field: False})
        with self.assertRaises(WudiTaskError):
            self.repo.release(t, {**receipt(t), "environment": "wrong"})
        self.repo.release(t, receipt(t))
        with self.assertRaises(WudiTaskError):
            self.repo.release(t, {**receipt(t), "checkpoint": "different"})

    def test_recovery_requires_explicit_authorization_and_preserves_checkpoint(self):
        old = ticket()
        self.repo.acquire(old)
        with self.assertRaises(WudiTaskError):
            self.repo.release(old, receipt(old), recovery=True)
        self.repo.release(old, receipt(old, recovery=True), recovery=True)
        new = ticket(environment="machine-b")
        self.repo.acquire(new)
        record = self.repo.inspect(old["actor_id"], old["task_id"])
        self.assertEqual(
            receipt(old)["checkpoint"], record["runs"][old["run_id"]]["checkpoint"]
        )

    def test_no_timeout_stealing(self):
        t = ticket()
        self.repo.acquire(t)
        with patch(
            "wuditask.coordination.utc_now", return_value="2099-01-01T00:00:00Z"
        ):
            with self.assertRaises(WudiTaskError):
                self.repo.acquire(ticket(environment="machine-b"))

    def test_migration_preserves_history_and_blocks_legacy_writer(self):
        old_root = self.base / "old"
        old_repo = make_repository(old_root)
        for task_id in ("WDT-20260711T120001Z-AAAAAA", "WDT-20260711T120002Z-BBBBBB"):
            add_task(old_repo, task_id)
        repo = ExecutionRepository(old_root)
        self.assertEqual(1, len(repo.inventory()["duplicates"]))
        original = {str(p): p.read_bytes() for p in old_root.rglob("data/open/*.json")}
        repo.initialize(
            {"actor_id": "12", "login": "alice"}, "All old writer clients stopped."
        )
        for p, contents in original.items():
            self.assertEqual(contents, Path(p).read_bytes())
        with self.assertRaises(WudiTaskError):
            old_repo.load_index()
        with self.assertRaises(WudiTaskError):
            add_task(old_repo, "WDT-20260711T120003Z-CCCCCC")
        self.assertFalse(repo.initialize({}, "retry")["changed"])

    def test_migration_refuses_live_legacy_runs(self):
        old_root = self.base / "live"
        old_repo = make_repository(old_root)
        task_id = "WDT-20260711T120001Z-AAAAAA"
        add_task(old_repo, task_id)
        path = old_root / "data/open" / f"{task_id}.json"
        raw = json.loads(path.read_text())
        raw["active_agents"] = [{"login": "alice", "run_id": new_run_id()}]
        path.write_text(json.dumps(raw))
        repo = ExecutionRepository(old_root)
        self.assertEqual(1, len(repo.inventory()["active"]))
        with self.assertRaises(WudiTaskError):
            repo.initialize({}, "claimed stopped")
        self.assertFalse((old_root / "coordination.json").exists())


class PersonalGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.origin = self.base / "remote.git"
        git(["init", "--bare", "--initial-branch=main", str(self.origin)], self.base)
        seed = self.base / "seed"
        seed.mkdir()
        git(["init", "-b", "main"], seed)
        git(["config", "user.name", "seed"], seed)
        git(["config", "user.email", "seed@example.invalid"], seed)
        make_repository(seed)
        git(["add", "hub.json"], seed)
        git(["commit", "-m", "legacy storage"], seed)
        git(["remote", "add", "origin", str(self.origin)], seed)
        git(["push", "origin", "main"], seed)
        self.coord = self.coordinator()
        self.coord.write(
            lambda repo: repo.initialize(
                {"actor_id": "12", "login": "alice"}, "All old clients stopped."
            ),
            ACTOR,
            lambda _: "initialize",
        )

    def tearDown(self):
        self.temp.cleanup()

    def coordinator(self, cls=GitCoordinator, **kwargs):
        return cls(
            remote=str(self.origin),
            branch="main",
            cache_root=self.base / "cache",
            repository_factory=ExecutionRepository,
            write_paths=("data", "hub.json", "coordination.json"),
            **kwargs,
        )

    def write(self, t, coord=None):
        return (coord or self.coord).write(
            lambda repo: repo.acquire(t), Identity(t["login"]), lambda _: "acquire"
        )

    def race(self, first, second):
        barrier = threading.Barrier(2)
        results, errors = [], []

        def before(attempt, _):
            if attempt == 1:
                barrier.wait(timeout=15)

        def attempt(t):
            try:
                results.append(self.write(t, self.coordinator(before_push=before)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt, args=(t,)) for t in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(25)
            self.assertFalse(thread.is_alive())
        return results, errors

    def test_two_environments_same_github_node_only_one_confirmed(self):
        results, errors = self.race(ticket(), ticket(environment="machine-b"))
        self.assertEqual(1, len(results))
        self.assertEqual(["execution_conflict"], [e.code for e in errors])

    def test_other_users_and_tasks_can_race(self):
        results, errors = self.race(ticket(), ticket(actor="13"))
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        results, errors = self.race(ticket(task="I_TWO"), ticket(task="I_THREE"))
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))

    def test_lost_push_response_retries_same_run(self):
        class LostResponse(GitCoordinator):
            def _push(self, checkout):
                result = super()._push(checkout)
                if result.returncode:
                    raise AssertionError(result.stderr)
                return subprocess.CompletedProcess(result.args, 1, "", "lost response")

        t = ticket()
        first = self.write(t, self.coordinator(LostResponse))
        self.assertEqual("commit_ancestry", first["sync"]["confirmation"])
        self.assertFalse(self.write(t)["changed"])
        with self.coord.snapshot() as repo:
            self.assertEqual(1, len(repo.inspect(t["actor_id"], t["task_id"])["runs"]))

    def test_unconfirmed_push_fails_closed(self):
        class UnknownPush(GitCoordinator):
            def _push(self, checkout):
                return subprocess.CompletedProcess([], 1, "", "offline")

        t = ticket()
        with self.assertRaises(WudiTaskError) as error:
            self.write(t, self.coordinator(UnknownPush))
        self.assertEqual("push_status_unknown", error.exception.code)
        self.write(t)
        with self.coord.snapshot() as repo:
            repo.assert_current(t)

    def test_migration_conflicts_with_stale_legacy_push(self):
        # A client that read the old manifest before cutover cannot push its old
        # branch after migration; refreshing then rejects the retired format.
        stale = self.base / "stale"
        git(["clone", str(self.origin), str(stale)], self.base)
        git(["checkout", "HEAD~1"], stale)
        old = TaskRepository(stale)
        add_task(old, "WDT-20260711T120001Z-AAAAAA")
        git(["add", "data"], stale)
        git(
            [
                "-c",
                "user.name=old",
                "-c",
                "user.email=old@example.invalid",
                "commit",
                "-m",
                "late old write",
            ],
            stale,
        )
        pushed = subprocess.run(
            ["git", "push", "origin", "HEAD:main"], cwd=stale, capture_output=True
        )
        self.assertNotEqual(0, pushed.returncode)
        with self.assertRaises(WudiTaskError):
            GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.base / "old-cache",
            ).write(
                lambda repo: add_task(repo, "WDT-20260711T120001Z-AAAAAA"),
                ACTOR,
                lambda _: "old write",
            )


if __name__ == "__main__":
    unittest.main()
