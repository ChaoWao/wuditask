from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuditask.dependencies import completion_is_ready, dependency_report
from wuditask.errors import DataValidationError
from wuditask.repository import HUB_SCHEMA_VERSION, TOOL_API_VERSION, TaskRepository
from wuditask.util import atomic_write_json, deletion_receipt_id
from wuditask.validation import validate_repository
from wuditask.workflow import create_task

from tests.helpers import ACTOR, RUN_ID, add_task, make_repository, spec

TASK_ID = "WDT-20260711T120007Z-888888"


class HubContractTests(unittest.TestCase):
    def test_manifest_and_published_schemas_are_versioned_together(self) -> None:
        self.assertEqual(3, HUB_SCHEMA_VERSION)
        self.assertEqual(4, TOOL_API_VERSION)
        root = Path(__file__).parents[1]
        task_schema = json.loads((root / "schemas/task.schema.json").read_text())
        receipt_schema = json.loads(
            (root / "schemas/deletion-receipt.schema.json").read_text()
        )
        hub_schema = json.loads((root / "schemas/hub.schema.json").read_text())
        self.assertEqual(3, task_schema["properties"]["schema_version"]["const"])
        self.assertEqual(2, receipt_schema["properties"]["receipt_version"]["const"])
        self.assertEqual(3, hub_schema["properties"]["schema_version"]["const"])
        self.assertEqual(4, hub_schema["properties"]["tool_api_version"]["const"])

    def test_dependency_readiness_uses_completion_evidence_not_acceptance_records(self) -> None:
        task = {
            "completion": {
                "outcome": "done",
                "evidence": ["tests passed"],
            }
        }
        self.assertEqual(
            (True, "archived as done with evidence"), completion_is_ready(task)
        )
        task["completion"]["evidence"] = []
        self.assertFalse(completion_is_ready(task)[0])

    def test_dependency_report_contains_coordination_fields_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            add_task(repository, TASK_ID)
            report = dependency_report(repository.load_index(), TASK_ID)["task"]
        self.assertIn("active_agents", report)
        self.assertNotIn("title", report)
        self.assertNotIn("goal", report)
        self.assertNotIn("acceptance_criteria", report)
        self.assertNotIn("claim_holder", report)

    def test_deletion_receipt_v2_uses_login_string_and_canonical_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            reason = "Erroneous record."
            receipt_id = deletion_receipt_id([TASK_ID], reason, ACTOR.login)
            atomic_write_json(
                repository.deletions_dir / f"{receipt_id}.json",
                {
                    "receipt_version": 2,
                    "id": receipt_id,
                    "task_ids": [TASK_ID],
                    "reason": reason,
                    "deleted_by": ACTOR.login,
                    "deleted_at": "2026-07-11T12:00:07Z",
                },
            )
            receipts = repository.load_deletion_receipts()
        self.assertEqual("alice", receipts[receipt_id]["deleted_by"])

    def test_receipt_with_numeric_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            receipt_id = "WDR-000000000000000000000000"
            atomic_write_json(
                repository.deletions_dir / f"{receipt_id}.json",
                {
                    "receipt_version": 2,
                    "id": receipt_id,
                    "task_ids": [TASK_ID],
                    "reason": "Erroneous record.",
                    "deleted_by": {"login": "alice", "github_id": 1001},
                    "deleted_at": "2026-07-11T12:00:07Z",
                },
            )
            with self.assertRaises(DataValidationError) as raised:
                repository.load_deletion_receipts()
        self.assertTrue(
            any(issue["path"].endswith("$.deleted_by") for issue in raised.exception.details["issues"])
        )

    def test_archived_participants_remain_valid_after_agents_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
            task["active_agents"] = []
            task["completion"] = {
                "outcome": "done",
                "completed_at": "2026-07-11T13:00:00Z",
                "completed_by": "alice",
                "result": "Done",
                "evidence": ["tests"],
                "participants": [{"login": "alice", "run_id": RUN_ID}],
            }
            atomic_write_json(repository.open_dir / f"{TASK_ID}.json", task)
            repository.archive(task)
            self.assertIn(TASK_ID, repository.load_index().archived)

    def test_missing_manifest_is_not_treated_as_an_empty_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))

            with self.assertRaises(DataValidationError) as raised:
                repository.load_index()

        self.assertEqual("hub.json", raised.exception.details["issues"][0]["path"])

    def test_manifest_requires_exact_current_versions(self) -> None:
        invalid_manifests = (
            {"schema_version": 2, "tool_api_version": 4},
            {"schema_version": 3, "tool_api_version": 3},
            {"schema_version": 3, "tool_api_version": 4, "legacy_mode": True},
            {},
        )
        for manifest in invalid_manifests:
            with (
                self.subTest(manifest=manifest),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / "hub.json").write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )

                with self.assertRaises(DataValidationError):
                    TaskRepository(root).load_index()

    def test_symlinked_data_path_is_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "hub"
            target = base / "outside"
            target.mkdir()
            repository = TaskRepository(root)
            repository.initialize()
            repository.open_dir.rmdir()
            repository.open_dir.symlink_to(target, target_is_directory=True)

            with self.assertRaises(DataValidationError) as raised:
                create_task(
                    repository,
                    spec("Symlink escape"),
                    ACTOR,
                    task_id="WDT-20260711T120006Z-777777",
                    now="2026-07-11T12:00:06Z",
                )

            self.assertEqual("data/open", raised.exception.details["issues"][0]["path"])
            self.assertEqual([], list(target.iterdir()))

    def test_deletion_receipt_and_live_task_may_not_share_an_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            add_task(repository, TASK_ID)
            reason = "This fixture should not coexist with a live task."
            receipt_id = deletion_receipt_id([TASK_ID], reason, ACTOR.login)
            atomic_write_json(
                repository.deletions_dir / f"{receipt_id}.json",
                {
                    "receipt_version": 2,
                    "id": receipt_id,
                    "task_ids": [TASK_ID],
                    "reason": reason,
                    "deleted_by": ACTOR.login,
                    "deleted_at": "2026-07-11T12:00:07Z",
                },
            )

            with self.assertRaises(DataValidationError) as raised:
                repository.load_index()

            self.assertTrue(
                any(
                    "deleted task ID still exists" in issue["message"]
                    for issue in raised.exception.details["issues"]
                )
            )

    def test_archived_tasks_must_have_valid_dependency_references(self) -> None:
        missing_id = "WDT-20260711T120009Z-AAAAAA"
        with tempfile.TemporaryDirectory() as temporary:
            repository = make_repository(Path(temporary))
            task = add_task(repository, TASK_ID)
            task["completion"] = {
                "outcome": "done",
                "completed_at": "2026-07-11T13:00:00Z",
                "completed_by": "alice",
                "result": "Archived for validation.",
                "evidence": ["Regression command passed."],
                "participants": [{"login": "alice", "run_id": RUN_ID}],
            }
            repository.archive(task)
            record = repository.load_index().archived[TASK_ID]
            task = record.task
            task["dependencies"] = [missing_id]
            atomic_write_json(record.path, task)

            with self.assertRaises(DataValidationError) as raised:
                validate_repository(repository)

        self.assertTrue(
            any(
                f"missing dependency {missing_id}" in issue["message"]
                for issue in raised.exception.details["issues"]
            )
        )


if __name__ == "__main__":
    unittest.main()
