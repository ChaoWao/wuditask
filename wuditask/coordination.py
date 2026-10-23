"""Personal execution rights; GitHub remains the business source of truth."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import WudiTaskError
from .repository import TaskRepository
from .util import RUN_ID_RE, atomic_write_json, read_json, utc_now

FORMAT = "github-personal-execution"
# Legacy clients reject this manifest before executing any queue mutation.
RETIRED_HUB = {"schema_version": 4, "tool_api_version": 6}


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WudiTaskError(code, message, exit_code=3)


def text_field(value: Any, name: str) -> str:
    require(
        isinstance(value, str) and bool(value.strip()),
        "invalid_execution_record",
        f"{name} must be nonempty text.",
    )
    return value


def execution_key(actor_id: str, task_id: str) -> str:
    text_field(actor_id, "actor ID")
    text_field(task_id, "task ID")
    return hashlib.sha256(json.dumps([actor_id, task_id]).encode()).hexdigest()


def validate_ticket(ticket: dict[str, Any]) -> None:
    require(
        isinstance(ticket, dict),
        "invalid_execution_record",
        "Session must be an object.",
    )
    for field in (
        "actor_id",
        "login",
        "task_id",
        "url",
        "environment",
        "session",
        "run_id",
    ):
        text_field(ticket.get(field), field)
    require(
        bool(RUN_ID_RE.fullmatch(ticket["run_id"])),
        "invalid_execution_record",
        "Invalid run ID.",
    )


class ExecutionRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        for path in (
            self.root / "data",
            self.root / "hub.json",
            self.root / "coordination.json",
        ):
            require(
                not path.is_symlink(),
                "unsafe_execution_path",
                "Execution paths cannot be symlinks.",
            )
        data = self.root / "data"
        if data.exists():
            require(
                not any(p.is_symlink() for p in data.rglob("*")),
                "unsafe_execution_path",
                "Execution data cannot contain symlinks.",
            )

    def inventory(self) -> dict[str, Any]:
        if (self.root / "coordination.json").exists():
            self._require_initialized()
            return {"initialized": True, "legacy": [], "duplicates": [], "active": []}
        if not (self.root / "hub.json").exists():
            require(
                not (self.root / "data").exists(),
                "unrecognized_execution_data",
                "Unrecognized data directory; inspect it before initialization.",
            )
            return {"initialized": False, "legacy": [], "duplicates": [], "active": []}
        index = TaskRepository(self.root).load_index()
        groups: dict[str, list[str]] = {}
        records = []
        active = []
        for task_id, record in index.all.items():
            source = record.task["source"]
            key = f"{source['repo'].casefold()}#{source['number']}"
            groups.setdefault(key, []).append(task_id)
            records.append(
                {
                    "id": task_id,
                    "source": source,
                    "sha256": hashlib.sha256(record.path.read_bytes()).hexdigest(),
                }
            )
            for run in record.task.get("active_agents", []):
                active.append({"task_id": task_id, **run})
        return {
            "initialized": False,
            "legacy": records,
            "duplicates": [ids for ids in groups.values() if len(ids) > 1],
            "active": active,
        }

    def initialize(self, actor: dict[str, str], evidence: str) -> dict[str, Any]:
        report = self.inventory()
        if report["initialized"]:
            return {"changed": False, "initialized": True}
        text_field(evidence, "old writer stop evidence")
        require(
            not report["active"],
            "legacy_runs_active",
            "Stop and release all legacy runs before migrating this remote.",
        )
        marker = {
            "format": FORMAT,
            "initialized_at": utc_now(),
            "initialized_by": actor,
            "migration": {"old_writers_stopped": evidence, **report},
        }
        atomic_write_json(self.root / "coordination.json", marker)
        atomic_write_json(self.root / "hub.json", RETIRED_HUB)
        atomic_write_json(
            self.root / "data" / "execution" / "migration.json", marker["migration"]
        )
        return {"changed": True, "initialized": True, "migration": marker["migration"]}

    def _require_initialized(self) -> None:
        marker = read_json(self.root / "coordination.json")
        require(
            isinstance(marker, dict)
            and marker.get("format") == FORMAT
            and read_json(self.root / "hub.json") == RETIRED_HUB,
            "execution_format_mismatch",
            "Initialize or migrate this remote with coord init before acquiring work.",
        )

    def _path(self, actor_id: str, task_id: str) -> Path:
        self._require_initialized()
        return (
            self.root
            / "data"
            / "execution"
            / "claims"
            / f"{execution_key(actor_id, task_id)}.json"
        )

    def inspect(self, actor_id: str, task_id: str) -> dict[str, Any]:
        path = self._path(actor_id, task_id)
        if not path.exists():
            return {
                "actor_id": actor_id,
                "task_id": task_id,
                "current": None,
                "runs": {},
            }
        record = read_json(path)
        require(
            isinstance(record, dict)
            and record.get("actor_id") == actor_id
            and record.get("task_id") == task_id
            and isinstance(record.get("runs"), dict),
            "invalid_execution_record",
            "Execution identity or history is invalid.",
        )
        for run_id, run in record["runs"].items():
            validate_ticket(run)
            require(
                run_id == run["run_id"]
                and run["actor_id"] == actor_id
                and run["task_id"] == task_id
                and run.get("state") in {"active", "quiescing", "released"}
                and isinstance(run.get("receipts"), dict),
                "invalid_execution_record",
                "Invalid run history.",
            )
        live = [
            rid for rid, run in record["runs"].items() if run["state"] != "released"
        ]
        require(
            live == ([] if record.get("current") is None else [record["current"]]),
            "invalid_execution_record",
            "Current execution does not match live runs.",
        )
        return record

    def _save(self, record: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        atomic_write_json(self._path(record["actor_id"], record["task_id"]), record)
        return {"changed": True, "run_id": run["run_id"], "run": deepcopy(run)}

    @staticmethod
    def _owned(record: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
        validate_ticket(ticket)
        run = record["runs"].get(ticket["run_id"])
        require(
            run is not None, "execution_not_found", "This run has never been acquired."
        )
        require(
            all(
                run[field] == ticket[field]
                for field in ("actor_id", "task_id", "environment", "session")
            ),
            "execution_session_mismatch",
            "Run belongs to another environment or session.",
        )
        return run

    def assert_current(self, ticket: dict[str, Any]) -> dict[str, Any]:
        record = self.inspect(ticket["actor_id"], ticket["task_id"])
        run = self._owned(record, ticket)
        require(
            record["current"] == ticket["run_id"] and run["state"] == "active",
            "execution_not_active",
            "Execution is no longer active; do not start or write.",
        )
        return deepcopy(run)

    def acquire(self, ticket: dict[str, Any]) -> dict[str, Any]:
        validate_ticket(ticket)
        record = self.inspect(ticket["actor_id"], ticket["task_id"])
        if ticket["run_id"] in record["runs"]:
            run = self.assert_current(ticket)
            return {"changed": False, "run_id": run["run_id"], "run": run}
        require(
            record["current"] is None,
            "execution_conflict",
            "This GitHub task already has an execution for this user; inspect it before continuing.",
        )
        run = {
            field: ticket[field]
            for field in (
                "actor_id",
                "login",
                "task_id",
                "url",
                "environment",
                "session",
                "run_id",
            )
        }
        run.update(state="active", acquired_at=utc_now(), receipts={})
        record["runs"][run["run_id"]] = run
        record["current"] = run["run_id"]
        return self._save(record, run)

    def quiesce(self, ticket: dict[str, Any]) -> dict[str, Any]:
        record = self.inspect(ticket["actor_id"], ticket["task_id"])
        run = self._owned(record, ticket)
        require(
            record["current"] == run["run_id"],
            "execution_not_active",
            "Cannot quiesce a retired run.",
        )
        if run["state"] == "quiescing":
            return {"changed": False, "run": deepcopy(run), "run_id": run["run_id"]}
        run.update(state="quiescing", quiesced_at=utc_now())
        return self._save(record, run)

    def release(
        self, ticket: dict[str, Any], receipt: dict[str, Any], *, recovery: bool = False
    ) -> dict[str, Any]:
        record = self.inspect(ticket["actor_id"], ticket["task_id"])
        if recovery:
            run = record["runs"].get(ticket["run_id"])
            require(
                run is not None,
                "execution_not_found",
                "Recovery must name an existing run.",
            )
        else:
            run = self._owned(record, ticket)
        require(
            isinstance(receipt, dict),
            "invalid_stop_receipt",
            "Stop receipt must be an object.",
        )
        for field in (
            "operation_id",
            "run_id",
            "actor_id",
            "task_id",
            "environment",
            "session",
            "checkpoint",
            "verified_by",
            "evidence",
        ):
            text_field(receipt.get(field), field)
        require(
            all(
                receipt[field] == run[field]
                for field in ("run_id", "actor_id", "task_id", "environment", "session")
            ),
            "invalid_stop_receipt",
            "Stop receipt must identify the exact old execution.",
        )
        require(
            receipt.get("method") == "manual-verification"
            and receipt.get("writers_stopped") is True
            and receipt.get("pending_operations_reconciled") is True,
            "stop_not_verified",
            "Manual verification must cover all writers, subagents, and pending external operations.",
        )
        if recovery:
            text_field(
                receipt.get("user_authorization"),
                "explicit user recovery authorization",
            )
        operation_id = receipt["operation_id"]
        payload = {"recovery": recovery, "stop": receipt}
        previous = run["receipts"].get(operation_id)
        if previous is not None:
            require(
                previous == payload,
                "operation_reuse",
                "An operation ID cannot be reused with different content.",
            )
            return {"changed": False, "run": deepcopy(run), "run_id": run["run_id"]}
        require(
            record["current"] == run["run_id"] and run["state"] != "released",
            "stale_execution",
            "Old execution cannot release its successor.",
        )
        require(
            recovery or run["state"] == "quiescing",
            "quiesce_required",
            "Quiesce the run before stopping writers and releasing it.",
        )
        run.update(
            state="released", released_at=utc_now(), checkpoint=receipt["checkpoint"]
        )
        run["receipts"][operation_id] = deepcopy(payload)
        record["current"] = None
        return self._save(record, run)
