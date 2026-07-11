from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuditask.errors import DataValidationError
from wuditask.repository import HUB_SCHEMA_VERSION, TOOL_API_VERSION, TaskRepository
from wuditask.util import atomic_write_json
from wuditask.validation import validate_repository
from wuditask.workflow import archive_task, claim_task, create_task

from tests.helpers import ACTOR, add_task, spec

ROOT = Path(__file__).resolve().parents[1]


class HubContractTests(unittest.TestCase):
    def test_public_hub_schema_matches_runtime_constants(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "hub.schema.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            HUB_SCHEMA_VERSION,
            schema["properties"]["schema_version"]["const"],
        )
        self.assertEqual(
            TOOL_API_VERSION,
            schema["properties"]["tool_api_version"]["const"],
        )
        self.assertEqual(
            {"schema_version", "tool_api_version"},
            set(schema["required"]),
        )
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])

    def test_missing_manifest_is_not_treated_as_an_empty_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))

            with self.assertRaises(DataValidationError) as raised:
                repository.load_index()

        self.assertEqual("hub.json", raised.exception.details["issues"][0]["path"])

    def test_manifest_requires_exact_current_versions(self) -> None:
        invalid_manifests = (
            {"schema_version": 2, "tool_api_version": 1},
            {"schema_version": 1, "tool_api_version": 2},
            {
                "schema_version": 1,
                "tool_api_version": 1,
                "legacy_mode": True,
            },
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

    def test_initialize_is_explicit_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = TaskRepository(root)
            repository.initialize()
            first = (root / "hub.json").read_text(encoding="utf-8")
            repository.initialize()

        self.assertEqual(
            first, '{\n  "schema_version": 1,\n  "tool_api_version": 1\n}\n'
        )

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

    def test_archived_tasks_must_have_valid_dependency_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = TaskRepository(Path(temporary))
            repository.initialize()
            task_id = "WDT-20260711T120008Z-999999"
            add_task(repository, task_id)
            claim_task(repository, ACTOR, task_id=task_id)
            archive_task(
                repository,
                ACTOR,
                task_id,
                outcome="done",
                result="Archived for validation.",
                evidence={"AC-1": "Regression command passed."},
                now="2026-07-11T13:00:00Z",
            )
            record = repository.load_index().archived[task_id]
            task = record.task
            task["dependencies"] = ["WDT-20260711T120009Z-AAAAAA"]
            atomic_write_json(record.path, task)

            with self.assertRaises(DataValidationError) as raised:
                validate_repository(repository)

        self.assertIn(
            "missing dependency",
            raised.exception.details["issues"][0]["message"],
        )


if __name__ == "__main__":
    unittest.main()
