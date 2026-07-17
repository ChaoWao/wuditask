from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wuditask.errors import DataValidationError, WudiTaskError
from wuditask.gitops import GitCoordinator
from wuditask.model import Identity
from wuditask.repository import TaskRepository
from wuditask.util import atomic_write_json, deletion_receipt_id
from wuditask.workflow import (
    archive_task,
    create_task,
    delete_archived_tasks,
    start_agent,
)

from tests.helpers import (
    ACTOR,
    OTHER_ACTOR,
    RUN_ID,
    add_task,
    git,
    make_hub_origin,
    spec,
)

FIRST_ID = "WDT-20260711T120000Z-111111"
SECOND_ID = "WDT-20260711T120001Z-222222"
THIRD_ID = "WDT-20260711T120002Z-333333"


def delivery(state: str) -> dict[str, object]:
    return {
        "status": "fresh",
        "delivery_state": state,
        "title": "Task",
        "body": "Body",
        "owners": ["alice"],
        "assignees": ["alice"],
        "prs": [],
        "updated_at": None,
        "fetched_at": "2026-07-11T13:00:00Z",
        "error": None,
        "url": "https://github.com/acme/service/issues/12",
    }


def archive(repository: TaskRepository, task_id: str, *, dependencies: list[str] | None = None) -> None:
    add_task(repository, task_id, dependencies=dependencies)
    with patch("wuditask.workflow.fetch_delivery", return_value=delivery("assigned")):
        start_agent(repository, ACTOR, task_id=task_id, run_id=RUN_ID)
    with patch(
        "wuditask.workflow.fetch_delivery",
        return_value=delivery("verification_needed"),
    ):
        archive_task(
            repository,
            ACTOR,
            task_id,
            outcome="done",
            result="Fixture completed.",
            evidence=["Fixture verification passed."],
            run_id=RUN_ID,
            now="2026-07-11T13:00:00Z",
        )


class DeleteWorkflowTests(unittest.TestCase):
    def test_deletion_receipt_v2_records_only_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            result = delete_archived_tasks(
                repository,
                ACTOR,
                [FIRST_ID],
                reason="Record was added by mistake.",
                now="2026-07-11T14:00:00Z",
            )

        self.assertEqual("alice", result["deleted_by"])
        self.assertEqual(2, result["deletion_receipt"]["receipt_version"])
        self.assertEqual("alice", result["deletion_receipt"]["deleted_by"])

    def test_delete_is_idempotent_for_same_login_casefold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            first = delete_archived_tasks(
                repository,
                ACTOR,
                [FIRST_ID],
                reason="Record was added by mistake.",
            )
            retry = delete_archived_tasks(
                repository,
                type(ACTOR)("Alice"),
                [FIRST_ID],
                reason="Record was added by mistake.",
            )

        self.assertTrue(retry["already_deleted"])
        self.assertEqual(first["deletion_receipt"], retry["deletion_receipt"])

    def test_external_dependents_block_deletion_but_internal_batch_edges_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            archive(repository, SECOND_ID, dependencies=[FIRST_ID])
            add_task(repository, THIRD_ID, dependencies=[SECOND_ID])
            with self.assertRaises(WudiTaskError) as raised:
                delete_archived_tasks(
                    repository,
                    ACTOR,
                    [FIRST_ID, SECOND_ID],
                    reason="Remove complete fixture chain.",
                )
            self.assertEqual("task_has_dependents", raised.exception.code)

            (repository.open_dir / f"{THIRD_ID}.json").unlink()
            result = delete_archived_tasks(
                repository,
                ACTOR,
                [FIRST_ID, SECOND_ID],
                reason="Remove complete fixture chain.",
            )
        self.assertEqual([FIRST_ID, SECOND_ID], result["deleted_task_ids"])

    def test_repository_validates_receipt_id_from_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            reason = "Not canonical"
            wrong = deletion_receipt_id([FIRST_ID], reason, "bob")
            atomic_write_json(
                repository.deletions_dir / f"{wrong}.json",
                {
                    "receipt_version": 2,
                    "id": wrong,
                    "task_ids": [FIRST_ID],
                    "reason": reason,
                    "deleted_by": "alice",
                    "deleted_at": "2026-07-11T14:00:00Z",
                },
            )
            with self.assertRaises(DataValidationError) as raised:
                repository.load_deletion_receipts()
        self.assertTrue(
            any(issue["path"].endswith("$.id") for issue in raised.exception.details["issues"])
        )

    def test_repository_restores_the_batch_when_an_unlink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            archive(repository, SECOND_ID)
            original_unlink = Path.unlink
            calls = 0

            def fail_second(path: Path, *args: object, **kwargs: object) -> None:
                nonlocal calls
                if path.suffix == ".json":
                    calls += 1
                    if calls == 2:
                        raise OSError("simulated unlink failure")
                original_unlink(path, *args, **kwargs)

            with (
                patch.object(Path, "unlink", new=fail_second),
                self.assertRaises(WudiTaskError) as raised,
            ):
                delete_archived_tasks(
                    repository,
                    ACTOR,
                    [FIRST_ID, SECOND_ID],
                    reason="Exercise rollback.",
                )

            self.assertEqual("archive_delete_failed", raised.exception.code)
            self.assertEqual(
                {FIRST_ID, SECOND_ID}, set(repository.load_index().archived)
            )
            self.assertEqual({}, repository.load_deletion_receipts())

    def test_incomplete_rollback_keeps_the_receipt_as_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            archive(repository, SECOND_ID)
            original_unlink = Path.unlink
            calls = 0

            def fail_second(path: Path, *args: object, **kwargs: object) -> None:
                nonlocal calls
                if path.suffix == ".json" and repository.archive_dir in path.parents:
                    calls += 1
                    if calls == 2:
                        raise OSError("simulated unlink failure")
                original_unlink(path, *args, **kwargs)

            def fail_restore(
                path: Path,
                _content: bytes,
                *args: object,
                **kwargs: object,
            ) -> int:
                raise OSError(f"simulated restore failure for {path.name}")

            with (
                patch.object(Path, "unlink", new=fail_second),
                patch.object(Path, "write_bytes", new=fail_restore),
                self.assertRaises(WudiTaskError) as raised,
            ):
                delete_archived_tasks(
                    repository,
                    ACTOR,
                    [FIRST_ID, SECOND_ID],
                    reason="Exercise incomplete rollback.",
                )

            self.assertEqual("archive_delete_failed", raised.exception.code)
            self.assertTrue(raised.exception.details["restore_failures"])
            receipts = list(repository.deletions_dir.glob("*.json"))
            self.assertEqual(1, len(receipts))
            self.assertFalse(
                repository.archive_dir.joinpath("2026", f"{FIRST_ID}.json").exists()
            )

    def test_deleted_task_ids_are_reserved_against_aba_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            delete_archived_tasks(
                repository,
                ACTOR,
                [FIRST_ID],
                reason="The record was erroneous.",
            )

            with self.assertRaises(WudiTaskError) as raised:
                create_task(
                    repository,
                    spec("Recreated task"),
                    ACTOR,
                    task_id=FIRST_ID,
                    now="2026-07-11T15:00:00Z",
                )

            self.assertEqual("task_id_deleted", raised.exception.code)

    def test_repository_rejects_a_receipt_for_a_different_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            archive(repository, FIRST_ID)
            reason = "The other record was erroneous."
            receipt = {
                "receipt_version": 2,
                "id": deletion_receipt_id([SECOND_ID], reason, ACTOR.login),
                "task_ids": [SECOND_ID],
                "reason": reason,
                "deleted_by": ACTOR.login,
                "deleted_at": "2026-07-11T15:00:00Z",
            }

            with self.assertRaises(WudiTaskError) as raised:
                repository.delete_archived([FIRST_ID], receipt)

            self.assertEqual("deletion_receipt_mismatch", raised.exception.code)
            self.assertIn(FIRST_ID, repository.load_index().archived)
            self.assertEqual({}, repository.load_deletion_receipts())


class DeleteGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.origin = make_hub_origin(self.base)
        seed = self.base / "hub-seed"
        archive(TaskRepository(seed), FIRST_ID)
        archive(TaskRepository(seed), SECOND_ID)
        git(["add", "data"], seed)
        git(["commit", "-m", "seed archived tasks"], seed)
        git(["push", "origin", "main"], seed)
        self.cache_root = self.base / "cache"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def coordinator(self, **kwargs: object) -> GitCoordinator:
        return GitCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
            **kwargs,
        )

    def remote_index(self) -> object:
        checkout = self.base / f"inspect-{len(list(self.base.glob('inspect-*')))}"
        git(["clone", str(self.origin), str(checkout)], self.base)
        return TaskRepository(checkout).load_index()

    def remote_repository(self, name: str) -> TaskRepository:
        checkout = self.base / name
        git(["clone", str(self.origin), str(checkout)], self.base)
        return TaskRepository(checkout)

    @staticmethod
    def operation(
        repository: TaskRepository,
        actor: Identity = ACTOR,
        reason: str = "Remove erroneous fixtures.",
        now: str | None = None,
    ) -> dict[str, object]:
        return delete_archived_tasks(
            repository,
            actor,
            [FIRST_ID, SECOND_ID],
            reason=reason,
            now=now,
        )

    @staticmethod
    def message(result: dict[str, object]) -> str:
        return (
            "wuditask: delete 2 archived task(s)\n\n"
            f"Tasks: {', '.join(result['deleted_task_ids'])}\n"
            f"Reason: {result['reason']}"
        )

    def test_lost_push_response_is_confirmed_by_commit_ancestry(self) -> None:
        class AmbiguousPushCoordinator(GitCoordinator):
            def _push(self, checkout: Path) -> subprocess.CompletedProcess[str]:
                accepted = super()._push(checkout)
                if accepted.returncode != 0:
                    raise AssertionError(accepted.stderr)
                return subprocess.CompletedProcess(
                    accepted.args,
                    1,
                    stdout=accepted.stdout,
                    stderr="simulated connection reset after accepted push",
                )

        result = AmbiguousPushCoordinator(
            remote=str(self.origin),
            branch="main",
            cache_root=self.cache_root,
        ).write(self.operation, ACTOR, self.message)

        self.assertTrue(result["sync"]["confirmed"])
        self.assertEqual("commit_ancestry", result["sync"]["confirmation"])
        self.assertEqual(result["sync"]["commit"], result["sync"]["remote_head"])
        self.assertEqual({}, self.remote_index().all)

    def test_non_fast_forward_rechecks_new_reverse_dependency(self) -> None:
        added = False

        def add_dependent(attempt: int, _checkout: Path) -> None:
            nonlocal added
            if attempt != 1 or added:
                return
            added = True
            other = GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.base / "other-cache",
            )
            other.write(
                lambda repository: create_task(
                    repository,
                    spec("Concurrent dependent", dependencies=[FIRST_ID]),
                    OTHER_ACTOR,
                    task_id=THIRD_ID,
                    now="2026-07-11T12:00:02Z",
                ),
                OTHER_ACTOR,
                lambda result: f"wuditask: add {result['task_id']}",
            )

        with self.assertRaises(WudiTaskError) as raised:
            self.coordinator(before_push=add_dependent).write(
                self.operation,
                ACTOR,
                self.message,
            )

        self.assertEqual("task_has_dependents", raised.exception.code)
        index = self.remote_index()
        self.assertIn(FIRST_ID, index.archived)
        self.assertIn(SECOND_ID, index.archived)
        self.assertIn(THIRD_ID, index.open)

    def test_identical_concurrent_delete_is_confirmed_after_rejection(self) -> None:
        deleted = False

        def delete_first(attempt: int, _checkout: Path) -> None:
            nonlocal deleted
            if attempt != 1 or deleted:
                return
            deleted = True
            other = GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.base / "other-cache",
            )
            other.write(
                lambda repository: self.operation(
                    repository,
                    now="2026-07-11T15:00:00Z",
                ),
                ACTOR,
                self.message,
            )

        result = self.coordinator(before_push=delete_first).write(
            lambda repository: self.operation(
                repository,
                now="2026-07-11T16:00:00Z",
            ),
            ACTOR,
            self.message,
        )

        self.assertTrue(result["already_deleted"])
        self.assertFalse(result["changed"])
        self.assertEqual(2, result["sync"]["attempts"])
        self.assertNotIn("confirmation", result["sync"])
        remote_receipt = next(
            iter(
                self.remote_repository("inspect-identical")
                .load_deletion_receipts()
                .values()
            )
        )
        self.assertEqual(remote_receipt, result["deletion_receipt"])
        self.assertEqual({}, self.remote_index().all)

    def test_different_concurrent_delete_cannot_claim_this_operation(self) -> None:
        deleted = False

        def delete_first(attempt: int, _checkout: Path) -> None:
            nonlocal deleted
            if attempt != 1 or deleted:
                return
            deleted = True
            other = GitCoordinator(
                remote=str(self.origin),
                branch="main",
                cache_root=self.base / "other-cache",
            )
            other.write(
                lambda repository: self.operation(
                    repository,
                    OTHER_ACTOR,
                    "Bob removed a different erroneous fixture batch.",
                ),
                OTHER_ACTOR,
                self.message,
            )

        with self.assertRaises(WudiTaskError) as raised:
            self.coordinator(before_push=delete_first).write(
                self.operation,
                ACTOR,
                self.message,
            )

        self.assertEqual("archived_tasks_required", raised.exception.code)
        receipts = self.remote_repository("inspect-different").load_deletion_receipts()
        self.assertEqual(1, len(receipts))
        receipt = next(iter(receipts.values()))
        self.assertEqual(OTHER_ACTOR.login, receipt["deleted_by"])
        self.assertEqual(
            "Bob removed a different erroneous fixture batch.",
            receipt["reason"],
        )


if __name__ == "__main__":
    unittest.main()
