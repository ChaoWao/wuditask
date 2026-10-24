"""Local execution plans: one manager writes, workers return candidate evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .coordination import require
from .execution_cli import journal_write, personal_root
from .gitops import _file_lock
from .util import read_json, utc_now

ACTIVE = {"prepared", "running", "launch_unknown", "awaiting_review"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


def identifier(value: str) -> str:
    require(
        isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)),
        "invalid_work_id",
        "Use a simple item/attempt identifier.",
    )
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def evidence(value: Any) -> None:
    require(
        isinstance(value, str) and bool(value.strip()),
        "evidence_required",
        "Provide a concrete evidence/reference, not an empty verdict.",
    )


def check_rights(plan: dict) -> None:
    if plan["mode"] == "local":
        require(
            bool(plan.get("local_reason")),
            "local_reason_required",
            "Local plans must explain why no GitHub claim applies.",
        )
        return
    from .execution_cli import (
        authenticated_actor,
        _load_config,
        _load_session,
        coordinator,
        _check,
    )

    actor = authenticated_actor()
    config = _load_config(actor)
    ticket = _load_session(Path(plan["session"]), actor, config)
    require(
        ticket["run_id"] == plan["run_id"],
        "plan_run_mismatch",
        "Adopt the plan explicitly after acquiring a new run.",
    )
    require(
        plan["task_ref"].rstrip("/").lower() == ticket["url"].rstrip("/").lower(),
        "plan_task_mismatch",
        "Use the canonical Issue/PR anchor URL from this S2 journal.",
    )
    _check(coordinator(config), ticket)


def validate(plan: dict) -> None:
    require(
        isinstance(plan, dict) and isinstance(plan.get("agreement"), dict),
        "invalid_plan",
        "Plan and agreement must be objects.",
    )
    for field in ("goal", "task_ref"):
        evidence(plan.get(field))
    for field in ("scope", "acceptance"):
        evidence(plan.get("agreement", {}).get(field))
    require(
        bool(plan.get("items")) and bool(plan.get("deliveries")),
        "empty_plan",
        "A plan needs work items and a complete delivery.",
    )
    plan.setdefault("decisions", {})
    for collection in ("items", "deliveries", "decisions"):
        require(
            isinstance(plan.get(collection), dict),
            "invalid_plan",
            f"{collection} must be an object keyed by ID.",
        )
        for key in plan[collection]:
            identifier(key)
            require(
                isinstance(plan[collection][key], dict),
                "invalid_plan",
                "Every entry must be an object.",
            )
    for key, item in plan["items"].items():
        evidence(item.get("title"))
        evidence(item.get("acceptance"))
        require(
            item.get("delivery") in plan["deliveries"],
            "unknown_delivery",
            f"{key} needs a delivery.",
        )
        require(
            item.get("access", "write") in {"read", "write"},
            "invalid_access",
            "access is read or write.",
        )
        require(
            isinstance(item.get("write_scope", ["."]), list)
            and bool(item.get("write_scope", ["."])),
            "invalid_write_scope",
            "Provide at least one relative scope.",
        )
        require(
            isinstance(item.get("resources", []), list)
            and all(isinstance(r, str) and r for r in item.get("resources", [])),
            "invalid_resources",
            "Resources must be nonempty string identities.",
        )
        require(
            isinstance(item.get("depends_on", []), list),
            "invalid_dependency",
            "Dependencies must be a list.",
        )
        for scope in item.get("write_scope", ["."]):
            require(
                isinstance(scope, str)
                and not Path(scope).is_absolute()
                and ".." not in Path(scope).parts,
                "invalid_write_scope",
                "Write scope must remain within its workspace.",
            )
        for edge in item.get("depends_on", []):
            require(
                isinstance(edge, dict),
                "invalid_dependency",
                "Dependency must be an object.",
            )
            kind = edge.get("kind")
            target = {
                "artifact": "items",
                "decision": "decisions",
                "merged": "deliveries",
            }.get(kind)
            require(
                target is not None and edge.get("ref") in plan[target],
                "unknown_dependency",
                f"Invalid dependency on {key}.",
            )
    visiting, visited = set(), set()

    def walk(key):
        require(key not in visiting, "plan_cycle", f"Dependency cycle includes {key}.")
        if key in visited:
            return
        visiting.add(key)
        for edge in plan["items"][key].get("depends_on", []):
            if edge["kind"] == "artifact":
                walk(edge["ref"])
            elif edge["kind"] == "merged":
                for predecessor, definition in plan["items"].items():
                    if definition["delivery"] == edge["ref"]:
                        walk(predecessor)
        visiting.remove(key)
        visited.add(key)

    for key in plan["items"]:
        walk(key)


def ready(plan: dict, item: dict) -> bool:
    if not plan["agreement"].get("approval_ref"):
        return False
    for edge in item.get("depends_on", []):
        ref, kind = edge["ref"], edge["kind"]
        if kind == "artifact" and plan["items"][ref]["status"] != "accepted":
            return False
        if kind == "decision" and plan["decisions"][ref]["status"] != "approved":
            return False
        if kind == "merged":
            delivery = plan["deliveries"][ref]
            expected = edge.get("merge_commit")
            if delivery["status"] not in {"merged", "baseline_verified"} or (
                expected
                and delivery.get("merge_evidence", {}).get("merge_commit") != expected
            ):
                return False
            if (
                edge.get("require_baseline", True)
                and delivery["status"] != "baseline_verified"
            ):
                return False
    return True


def refresh(plan: dict) -> None:
    for item in plan["items"].values():
        if item["status"] in {"planned", "ready", "needs_rework"}:
            item["status"] = "ready" if ready(plan, item) else "planned"
    statuses = [i["status"] for i in plan["items"].values()]
    if any(a["status"] in ACTIVE for a in plan["attempts"].values()):
        plan["summary_state"] = "active"
    elif "ready" in statuses:
        plan["summary_state"] = "actionable"
    elif all(
        d["status"] == "baseline_verified" for d in plan["deliveries"].values()
    ) and all(s == "accepted" for s in statuses):
        plan["summary_state"] = "completed"
    elif any(d["status"] == "ready_for_merge" for d in plan["deliveries"].values()):
        plan["summary_state"] = "awaiting_merge"
    elif any(d["status"] == "pending" for d in plan["decisions"].values()) or not plan[
        "agreement"
    ].get("approval_ref"):
        plan["summary_state"] = "awaiting_decision"
    else:
        plan["summary_state"] = "blocked_or_validation_needed"


class PlanStore:
    def __init__(
        self,
        root: Path,
        *,
        registry: Path | None = None,
        checker: Callable = check_rights,
    ):
        self.root = root.expanduser().resolve()
        self.registry = registry or personal_root() / "work-reservations"
        self.checker = checker

    def load(self) -> dict:
        return read_json(self.root / "plan.json")

    def _save(self, plan: dict) -> None:
        refresh(plan)
        plan["updated_at"] = utc_now()
        journal_write(self.root / "plan.json", plan)

    def init(
        self,
        spec: dict,
        *,
        session: Path | None = None,
        local_reason: str | None = None,
    ) -> dict:
        require(
            bool(session) != bool(local_reason),
            "plan_binding_required",
            "Choose a S2 journal or an explicit local maintenance/design reason.",
        )
        require(isinstance(spec, dict), "invalid_plan", "Plan must be a JSON object.")
        plan = deepcopy(spec)
        validate(plan)
        plan.update(
            plan_id=uuid.uuid4().hex,
            revision=1,
            mode="coordinated" if session else "local",
            session=str(session.resolve()) if session else None,
            local_reason=local_reason,
            run_id=read_json(session)["run_id"]
            if session
            else "local-" + uuid.uuid4().hex,
            attempts={},
            processed_events={},
            history=[],
            decisions=plan.get("decisions", {}),
        )
        if local_reason:
            require(
                not re.match(
                    r"https://github.com/[^/]+/[^/]+/(issues|pull)/",
                    plan.get("task_ref", ""),
                ),
                "claim_required",
                "GitHub tasks use a S2 journal, not local mode.",
            )
        for key, item in plan.get("items", {}).items():
            item.update(id=key, status="planned", active_attempt=None)
        for key, delivery in plan.get("deliveries", {}).items():
            delivery.update(id=key, status="implementing", head=None)
        for key, decision in plan["decisions"].items():
            decision.update(id=key, status="pending")
        validate(plan)
        self.checker(plan)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with _file_lock(self.root / "plan.lock"):
            require(
                not (self.root / "plan.json").exists(),
                "plan_exists",
                "Resume this plan; do not replace it.",
            )
            self._save(plan)
        return plan

    def mutate(self, expected: int, operation: Callable[[dict], Any]) -> dict:
        self.checker(self.load())
        self.registry.mkdir(parents=True, mode=0o700, exist_ok=True)
        with (
            _file_lock(self.registry / "dispatch.lock"),
            _file_lock(self.root / "plan.lock"),
        ):
            plan = self.load()
            self.checker(plan)
            require(
                plan["revision"] == expected,
                "stale_plan_revision",
                "Read the current plan before editing.",
            )
            operation(plan)
            validate(plan)
            plan["revision"] += 1
            self._save(plan)
            self._release_reviewed(plan)
            return plan

    def decision(self, key: str, status: str, reference: str, expected: int) -> dict:
        evidence(reference)
        require(
            status in {"approved", "rejected", "pending"},
            "invalid_decision",
            "Invalid decision status.",
        )

        def change(plan):
            require(key in plan["decisions"], "unknown_decision", key)
            self._invalidate(
                plan,
                {
                    i
                    for i, v in plan["items"].items()
                    if any(
                        e["kind"] == "decision" and e["ref"] == key
                        for e in v.get("depends_on", [])
                    )
                },
            )
            plan["decisions"][key].update(
                status=status, evidence=reference, observed_at=utc_now()
            )

        return self.mutate(expected, change)

    def _invalidate(self, plan: dict, affected: set) -> None:
        while True:
            more = {
                k
                for k, v in plan["items"].items()
                if any(
                    e["kind"] == "artifact" and e["ref"] in affected
                    for e in v.get("depends_on", [])
                )
            }
            if more <= affected:
                break
            affected |= more
        require(
            not any(
                plan["deliveries"][plan["items"][k]["delivery"]]["status"]
                in {"merged", "baseline_verified"}
                for k in affected
            ),
            "landed_contract_immutable",
            "Keep landed acceptance history; use a new plan for additional work.",
        )
        require(
            not any(
                plan["items"][k].get("active_attempt")
                and plan["attempts"][plan["items"][k]["active_attempt"]]["status"]
                in ACTIVE
                for k in affected
            ),
            "affected_attempt_active",
            "Stop/reconcile affected attempts before changing their contract; unrelated work may continue.",
        )
        for key in affected:
            item = plan["items"][key]
            if item["status"] != "cancelled":
                item["status"] = "planned"
            d = plan["deliveries"][item["delivery"]]
            if d["status"] == "ready_for_merge":
                d["status"] = "validating"

    def edit_item(self, key: str, fields: dict, expected: int) -> dict:
        allowed = {
            "title",
            "acceptance",
            "depends_on",
            "write_scope",
            "resources",
            "summary",
            "access",
            "status",
        }
        require(
            set(fields) <= allowed,
            "invalid_item_edit",
            "Edit task definition fields only.",
        )
        if "status" in fields:
            require(
                fields["status"] in {"planned", "blocked", "cancelled"},
                "invalid_item_status",
                "Use review to accept results.",
            )

        def change(plan):
            require(key in plan["items"], "unknown_item", key)
            self._invalidate(plan, {key})
            plan["items"][key].update(fields)

        return self.mutate(expected, change)

    def _reservation_path(self, attempt: str) -> Path:
        return self.registry / (digest([str(self.root), attempt]) + ".json")

    def _reserve(self, item: dict, attempt: dict) -> None:
        cwd = Path(attempt["workspace"])
        scopes = [str((cwd / s).resolve()) for s in item.get("write_scope", ["."])]
        resources = list(item.get("resources", [])) + [
            "agent-session:" + attempt["session_id"]
        ]
        # Worktrees share branch refs even when file paths are disjoint.
        if item.get("access", "write") == "write":
            git = subprocess.run(
                [
                    "git",
                    "-C",
                    str(cwd),
                    "rev-parse",
                    "--git-common-dir",
                    "--symbolic-full-name",
                    "HEAD",
                ],
                capture_output=True,
                text=True,
            )
            if git.returncode == 0:
                lines = git.stdout.strip().splitlines()
                if len(lines) == 2 and lines[1] != "HEAD":
                    resources.append(
                        "git-ref:" + str((cwd / lines[0]).resolve()) + ":" + lines[1]
                    )
        access = item.get("access", "write")
        for path in self.registry.glob("*.json"):
            old = read_json(path)
            if not old.get("active"):
                continue
            conflict = set(resources) & set(old["resources"])
            overlap = any(
                a == b or a in b.parents or b in a.parents
                for a in map(Path, scopes)
                for b in map(Path, old["scopes"])
            )
            require(
                not conflict and not (overlap and "write" in {access, old["access"]}),
                "work_scope_busy",
                f"Overlapping workspace/resource held by {old['plan']} attempt {old['attempt_id']}.",
            )
        journal_write(
            self._reservation_path(attempt["attempt_id"]),
            dict(
                active=True,
                plan=str(self.root),
                attempt_id=attempt["attempt_id"],
                scopes=scopes,
                resources=resources,
                attempt_record=attempt,
                access=access,
            ),
        )

    def prepare(
        self,
        item_id: str,
        attempt_id: str,
        backend: str,
        workspace: Path,
        prompt: str,
        preflight: dict,
        expected: int,
        continue_attempt: str | None = None,
    ) -> dict:
        identifier(attempt_id)
        require(
            backend in {"codex-native", "claude", "codex"}, "unknown_executor", backend
        )
        require(workspace.is_dir(), "workspace_missing", "Workspace must exist.")
        evidence(prompt)
        require(
            preflight.get("decision") == "proceed"
            and preflight.get("prompt_sha256")
            == hashlib.sha256(prompt.encode()).hexdigest(),
            "preflight_mismatch",
            "Bind a proceed review to the exact prompt.",
        )
        for field in ("main_evidence", "open_pr_disposition", "observed_at"):
            evidence(preflight.get(field))
        assignment_key = digest(
            [
                item_id,
                backend,
                str(workspace.resolve()),
                prompt,
                preflight,
                continue_attempt,
            ]
        )
        current = self.load()
        if attempt_id in current["attempts"]:
            self.checker(current)
            require(
                current["attempts"][attempt_id]["assignment_key"] == assignment_key,
                "attempt_collision",
                "Attempt ID already belongs to another assignment.",
            )
            return current

        def change(plan):
            require(item_id in plan["items"], "unknown_item", item_id)
            item = plan["items"][item_id]
            require(
                item["status"] == "ready" and ready(plan, item),
                "item_not_ready",
                "Dependencies/approval are not satisfied.",
            )
            require(
                not item.get("active_attempt")
                or plan["attempts"][item["active_attempt"]]["status"] not in ACTIVE,
                "item_active",
                "Do not start a second writer.",
            )
            directory = self.root / "attempts" / item_id / attempt_id
            require(
                not directory.exists(),
                "attempt_orphaned",
                "Existing attempt files require reconciliation, not overwrite.",
            )
            attempt = dict(
                attempt_id=attempt_id,
                item_id=item_id,
                backend=backend,
                workspace=str(workspace.resolve()),
                status="prepared",
                assignment_key=assignment_key,
                directory=str(directory),
                run_id=plan["run_id"],
                session_id=str(uuid.uuid4()),
                created_at=utc_now(),
            )
            if continue_attempt:
                require(
                    continue_attempt in plan["attempts"],
                    "unknown_attempt",
                    continue_attempt,
                )
                previous = plan["attempts"][continue_attempt]
                require(
                    previous["status"] == "reviewed"
                    and previous.get("observation", {}).get("state") == "exited"
                    and previous["backend"] == backend
                    and previous["workspace"] == attempt["workspace"]
                    and previous["item_id"] == item_id
                    and backend != "codex",
                    "continuation_invalid",
                    "Continue the exited original author in the same workspace/backend/item.",
                )
                require(
                    backend != "codex-native" or bool(previous.get("handle")),
                    "continuation_invalid",
                    "Native continuation needs the saved original author handle.",
                )
                attempt.update(
                    session_id=previous["session_id"],
                    continue_attempt=continue_attempt,
                    previous_handle=previous.get("handle"),
                )
            self._reserve(item, attempt)
            directory.mkdir(parents=True, mode=0o700)
            journal_write(
                directory / "assignment.json",
                dict(
                    plan_id=plan["plan_id"],
                    run_id=plan["run_id"],
                    item_id=item_id,
                    attempt_id=attempt_id,
                    plan_revision=plan["revision"],
                    base=preflight["main_evidence"],
                    preflight=preflight,
                    acceptance=item["acceptance"],
                    write_scope=item.get("write_scope", ["."]),
                ),
            )
            (directory / "prompt.md").write_text(prompt)
            plan["attempts"][attempt_id] = attempt
            item.update(status="running", active_attempt=attempt_id)

        return self.mutate(expected, change)

    def observe(
        self,
        attempt_id: str,
        state: str,
        reference: str,
        expected: int,
        handle: str | None = None,
    ) -> dict:
        evidence(reference)
        require(
            state in {"running", "exited", "not_started", "unknown"},
            "invalid_observation",
            state,
        )

        def change(plan):
            require(attempt_id in plan["attempts"], "unknown_attempt", attempt_id)
            attempt = plan["attempts"][attempt_id]
            require(
                attempt["status"] in ACTIVE,
                "attempt_terminal",
                "Historical attempt cannot become active again.",
            )
            if attempt["backend"] != "codex-native":
                from .agent_executor import inspect

                observed = inspect(Path(attempt["directory"]))["status"]
                require(
                    state == observed,
                    "executor_observation_mismatch",
                    "Use actual process observation, not a claimed exit.",
                )
            if handle:
                require(
                    not attempt.get("previous_handle")
                    or handle == attempt["previous_handle"],
                    "handle_mismatch",
                    "Continuation must use the original native author; use a fresh attempt for a different agent.",
                )
                require(
                    not attempt.get("handle") or attempt["handle"] == handle,
                    "handle_mismatch",
                    "Do not replace an existing executor identity.",
                )
                attempt["handle"] = handle
            attempt.update(
                observation=dict(state=state, evidence=reference, observed_at=utc_now())
            )
            if state == "running":
                attempt["status"] = "running"
            elif state == "unknown":
                attempt["status"] = "launch_unknown"

        return self.mutate(expected, change)

    def report(self, attempt_id: str, result: dict) -> dict:
        plan = self.load()
        require(attempt_id in plan["attempts"], "unknown_attempt", attempt_id)
        attempt = plan["attempts"][attempt_id]
        require(
            result.get("attempt_id") == attempt_id
            and result.get("run_id") == attempt["run_id"]
            and result.get("item_id") == attempt["item_id"],
            "result_identity_mismatch",
            "Result belongs to another attempt/run.",
        )
        require(
            result.get("outcome")
            in {"candidate_ready", "failed", "blocked", "cancelled"},
            "invalid_result",
            "Worker results are candidates, never accepted.",
        )
        evidence(result.get("summary"))
        directory = Path(attempt["directory"])
        with _file_lock(directory / "report.lock"):
            path = directory / "result.json"
            if (directory / "candidate-event.json").exists():
                self._verified_results(plan, attempt)
            if path.exists():
                require(
                    read_json(path) == result,
                    "result_conflict",
                    "Keep the first immutable result; use a new attempt for changes.",
                )
            else:
                journal_write(path, result)
            event = dict(
                event_id=attempt_id + "-candidate",
                attempt_id=attempt_id,
                run_id=attempt["run_id"],
                result_path="result.json",
                result_digest=digest(result),
            )
            if not (directory / "candidate-event.json").exists():
                journal_write(directory / "candidate-event.json", event)
        return event

    def poll(self) -> dict:
        plan = self.load()
        pending = []
        historical = []
        for attempt_id, attempt in plan["attempts"].items():
            directory = Path(attempt["directory"])
            for name in ("candidate-event.json", "completion.json"):
                if (directory / name).exists():
                    event = read_json(directory / name)
                    event_id = attempt_id + ":" + name
                    if event_id not in plan["processed_events"]:
                        destination = (
                            pending
                            if attempt["status"] in ACTIVE
                            and attempt["run_id"] == plan["run_id"]
                            and plan["items"][attempt["item_id"]].get("active_attempt")
                            == attempt_id
                            else historical
                        )
                        destination.append(
                            dict(
                                event_id=event_id,
                                attempt_id=attempt_id,
                                path=str(directory / name),
                                event=event,
                                current=attempt["status"] in ACTIVE
                                and attempt["run_id"] == plan["run_id"]
                                and plan["items"][attempt["item_id"]].get(
                                    "active_attempt"
                                )
                                == attempt_id,
                            )
                        )
        return dict(
            plan=plan,
            pending_events=pending,
            historical_events=historical,
            ready_items=[k for k, v in plan["items"].items() if v["status"] == "ready"],
        )

    def review(
        self, attempt_id: str, verdict: str, reference: str, expected: int
    ) -> dict:
        evidence(reference)
        require(
            verdict in {"accepted", "needs_rework", "blocked", "cancelled"},
            "invalid_verdict",
            verdict,
        )

        def change(plan):
            require(attempt_id in plan["attempts"], "unknown_attempt", attempt_id)
            attempt = plan["attempts"][attempt_id]
            item = plan["items"][attempt["item_id"]]
            require(
                item["active_attempt"] == attempt_id
                and attempt["run_id"] == plan["run_id"],
                "stale_attempt",
                "Old results require explicit adoption, never overwrite current work.",
            )
            require(
                attempt["status"] in ACTIVE,
                "attempt_terminal",
                "Attempt already reviewed.",
            )
            directory = Path(attempt["directory"])
            observation = attempt.get("observation", {}).get("state")
            require(
                observation in {"exited", "not_started"},
                "exit_unconfirmed",
                "Confirm native/external executor exit before retiring its write scope.",
            )
            results = self._verified_results(plan, attempt)
            if attempt["backend"] != "codex-native":
                from .agent_executor import inspect

                require(
                    inspect(directory)["status"] in {"exited", "not_started"},
                    "exit_unconfirmed",
                    "External executor still running or unknown.",
                )
            if verdict == "accepted":
                require(
                    observation == "exited",
                    "exit_unconfirmed",
                    "An unstarted attempt cannot be accepted.",
                )
                require(
                    any(r.get("outcome") == "candidate_ready" for r in results)
                    and all(
                        r.get("outcome") in {"candidate_ready", "candidate"}
                        for r in results
                    ),
                    "result_not_ready",
                    "No successful candidate, or an executor/result failure exists.",
                )
            item.update(status=verdict, summary=reference)
            attempt.update(
                status="reviewed", verdict=verdict, review_evidence=reference
            )
            for name in ("candidate-event.json", "completion.json"):
                if (directory / name).exists():
                    plan["processed_events"][attempt_id + ":" + name] = dict(
                        verdict=verdict, evidence=reference, at=utc_now()
                    )

        return self.mutate(expected, change)

    def delivery(self, key: str, update: dict, expected: int) -> dict:
        allowed = {
            "head",
            "pr_url",
            "review_evidence",
            "ci_evidence",
            "merge_evidence",
            "baseline_evidence",
            "status",
            "summary",
        }
        require(
            set(update) <= allowed,
            "invalid_delivery_edit",
            "Only delivery evidence/status may be updated.",
        )

        def change(plan):
            require(key in plan["deliveries"], "unknown_delivery", key)
            d = plan["deliveries"][key]
            if d["status"] in {"merged", "baseline_verified"}:
                require(
                    update.get("head", d.get("head")) == d.get("head")
                    and update.get("status", d["status"])
                    in (
                        {"baseline_verified"}
                        if d["status"] == "baseline_verified"
                        else {"merged", "baseline_verified"}
                    ),
                    "landed_delivery_immutable",
                    "Record new work in a new plan; do not erase an observed merge.",
                )
                if "merge_evidence" in update:
                    require(
                        update["merge_evidence"].get("merge_commit")
                        == d.get("merge_evidence", {}).get("merge_commit"),
                        "landed_delivery_immutable",
                        "An observed merge commit cannot be replaced.",
                    )
            if update.get("head") and update["head"] != d.get("head"):
                d.pop("review_evidence", None)
                d.pop("ci_evidence", None)
                d.pop("merge_evidence", None)
                d.pop("baseline_evidence", None)
                d["status"] = "validating"
                self._invalidate(
                    plan,
                    {
                        i
                        for i, v in plan["items"].items()
                        if any(
                            e["kind"] == "merged" and e["ref"] == key
                            for e in v.get("depends_on", [])
                        )
                    },
                )
            d.update(update)
            require(
                d["status"]
                in {
                    "implementing",
                    "validating",
                    "ready_for_merge",
                    "merged",
                    "baseline_verified",
                },
                "invalid_delivery_state",
                d["status"],
            )
            if d["status"] == "ready_for_merge":
                require(
                    all(
                        i["status"] == "accepted"
                        for i in plan["items"].values()
                        if i["delivery"] == key
                    ),
                    "delivery_incomplete",
                    "Every required item must be accepted.",
                )
                for kind in ("review_evidence", "ci_evidence"):
                    e = d.get(kind, {})
                    require(
                        bool(d.get("head"))
                        and e.get("head") == d["head"]
                        and e.get("status") == "passed",
                        "delivery_evidence_stale",
                        f"{kind} must pass on current head.",
                    )
                    evidence(e.get("reference"))
                    evidence(e.get("observed_at"))
            if d["status"] in {"merged", "baseline_verified"}:
                e = d.get("merge_evidence", {})
                evidence(e.get("reference"))
                evidence(e.get("merge_commit"))
                require(
                    bool(d.get("head")) and e.get("head") == d["head"],
                    "merge_evidence_mismatch",
                    "Verify actual merged head, not an old PR observation.",
                )
            if d["status"] == "baseline_verified":
                require(
                    all(
                        i["status"] == "accepted"
                        for i in plan["items"].values()
                        if i["delivery"] == key
                    ),
                    "delivery_incomplete",
                    "Baseline verification cannot bypass required work.",
                )
                e = d.get("baseline_evidence", {})
                require(
                    e.get("merge_commit") == d["merge_evidence"]["merge_commit"]
                    and e.get("status") == "passed",
                    "baseline_unverified",
                    "Baseline evidence must match the actual merge.",
                )
                evidence(e.get("reference"))

        return self.mutate(expected, change)

    def approve(self, reference: str, expected: int) -> dict:
        evidence(reference)
        return self.mutate(
            expected, lambda p: p["agreement"].update(approval_ref=reference)
        )

    def _verified_results(self, plan: dict, attempt: dict) -> list[dict]:
        directory = Path(attempt["directory"])
        results = []
        for filename, event_name in (
            ("result.json", "candidate-event.json"),
            ("executor-result.json", "completion.json"),
        ):
            path, event_path = directory / filename, directory / event_name
            if not path.exists():
                require(
                    not event_path.exists(),
                    "result_missing",
                    "Completion references a missing result.",
                )
                continue
            require(
                event_path.exists(),
                "result_uncommitted",
                "Reconcile the result publication before review.",
            )
            require(
                not path.is_symlink() and not event_path.is_symlink(),
                "invalid_result_path",
                "Results must remain regular files in their attempt directory.",
            )
            result, event = read_json(path), read_json(event_path)
            require(
                isinstance(result, dict) and isinstance(event, dict),
                "invalid_result",
                "Result and completion event must be objects.",
            )
            for field in ("attempt_id", "run_id", "item_id"):
                require(
                    result.get(field) == attempt[field],
                    "result_identity_mismatch",
                    "Result identity does not match assignment.",
                )
            require(
                event.get("attempt_id") == attempt["attempt_id"]
                and event.get("run_id") == attempt["run_id"],
                "result_identity_mismatch",
                "Event identity does not match assignment.",
            )
            expected = (
                digest(result)
                if filename == "result.json"
                else hashlib.sha256(path.read_bytes()).hexdigest()
            )
            require(
                event.get("result_path") == filename
                and event.get("result_digest") == expected,
                "result_digest_mismatch",
                "Published result was modified or points elsewhere.",
            )
            if filename == "executor-result.json":
                require(
                    result.get("plan_id") == plan["plan_id"]
                    and result.get("accepted") is False,
                    "invalid_executor_result",
                    "Executor cannot accept its own output.",
                )
            results.append(result)
        return results

    def _release_reviewed(self, plan: dict) -> None:
        # Release only after the reviewed state is durable; a crash leaves a safe reservation.
        for attempt_id, attempt in plan["attempts"].items():
            path = self._reservation_path(attempt_id)
            if attempt["status"] == "reviewed" and path.exists():
                reservation = read_json(path)
                if reservation.get("active"):
                    reservation["active"] = False
                    journal_write(path, reservation)

    def launch(
        self,
        attempt_id: str,
        *,
        wake_command: list[str] | None = None,
        launcher: str | None = None,
    ) -> dict:
        from . import agent_executor

        self.checker(self.load())
        with _file_lock(self.root / "plan.lock"):
            plan = self.load()
            self.checker(plan)
            require(attempt_id in plan["attempts"], "unknown_attempt", attempt_id)
            attempt = plan["attempts"][attempt_id]
            require(
                attempt["run_id"] == plan["run_id"] and attempt["status"] in ACTIVE,
                "stale_attempt",
                "Only current active attempts may launch.",
            )
            require(
                attempt["backend"] != "codex-native",
                "native_host_required",
                "Spawn with the host native subagent API, then record its handle with observe.",
            )
            directory = Path(attempt["directory"])
            assignment = read_json(directory / "assignment.json")
            require(
                hashlib.sha256((directory / "prompt.md").read_bytes()).hexdigest()
                == assignment["preflight"]["prompt_sha256"],
                "prompt_modified",
                "Prepared prompt changed; create a new attempt.",
            )
            command = None
            if plan["mode"] == "coordinated":
                import sys

                command = [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "tools/wuditask.py"),
                    "--json",
                    "coord",
                    "check",
                    "--session",
                    plan["session"],
                ]
            result = agent_executor.launch(
                directory,
                attempt["backend"],
                Path(attempt["workspace"]),
                directory / "prompt.md",
                attempt["session_id"],
                command,
                wake_command,
                resume=bool(attempt.get("continue_attempt")),
                launcher=launcher,
            )
            attempt["status"] = (
                "launch_unknown" if result["status"] == "launch_unknown" else "running"
            )
            plan["revision"] += 1
            self._save(plan)
            return result

    def reconcile(self) -> dict:
        from . import agent_executor

        plan = self.load()
        self.checker(plan)
        observations = {}
        for key, a in plan["attempts"].items():
            if (
                a["status"] in ACTIVE
                and a["run_id"] == plan["run_id"]
                and a["backend"] != "codex-native"
            ):
                observations[key] = agent_executor.reconcile(Path(a["directory"]))

        def change(current):
            # A prepare crash may leave a reservation before the plan commit.
            # Recover its identity conservatively; never infer that it did not start.
            for path in self.registry.glob("*.json"):
                reservation = read_json(path)
                orphan = reservation.get("attempt_record")
                if (
                    reservation.get("active")
                    and reservation.get("plan") == str(self.root)
                    and orphan
                    and orphan["attempt_id"] not in current["attempts"]
                ):
                    item = current["items"][orphan["item_id"]]
                    require(
                        not item.get("active_attempt")
                        or current["attempts"][item["active_attempt"]]["status"]
                        not in ACTIVE,
                        "orphan_conflict",
                        "Reconcile current writer before adopting an orphan attempt.",
                    )
                    orphan["status"] = "launch_unknown"
                    current["attempts"][orphan["attempt_id"]] = orphan
                    item.update(status="running", active_attempt=orphan["attempt_id"])
                    Path(orphan["directory"]).mkdir(parents=True, exist_ok=True)
            for key, state in observations.items():
                a = current["attempts"][key]
                a["observation"] = {
                    "state": state["status"],
                    "evidence": str(Path(a["directory"]) / "launch.json"),
                    "observed_at": utc_now(),
                }
                if state["status"] == "unknown":
                    a["status"] = "launch_unknown"
                elif state["status"] == "exited":
                    a["status"] = "awaiting_review"

        self.mutate(plan["revision"], change)
        return self.poll()

    def adopt(
        self,
        source: Path,
        reference: str,
        *,
        session: Path | None = None,
        local_reason: str | None = None,
    ) -> dict:
        """Copy definitions into a new execution; evidence is preserved, never auto-accepted."""
        evidence(reference)
        previous = read_json(source.resolve() / "plan.json")
        spec = {
            k: deepcopy(previous[k])
            for k in (
                "goal",
                "task_ref",
                "agreement",
                "items",
                "decisions",
                "deliveries",
            )
        }
        spec["agreement"].pop("approval_ref", None)
        for value in spec["deliveries"].values():
            for field in (
                "review_evidence",
                "ci_evidence",
                "merge_evidence",
                "baseline_evidence",
            ):
                value.pop(field, None)
        plan = self.init(spec, session=session, local_reason=local_reason)

        def record(current):
            current["adopted_from"] = {
                "plan_id": previous["plan_id"],
                "run_id": previous["run_id"],
                "path": str(source.resolve()),
                "evidence": reference,
            }
            current["plan_id"] = previous["plan_id"]
            # Archived snapshot is read-only context. Old events are not in the new inbox.
            journal_write(self.root / "previous-plan.json", previous)
            for key, item in current["items"].items():
                old = previous["items"][key]
                if (
                    old.get("active_attempt")
                    and previous["attempts"][old["active_attempt"]]["status"] in ACTIVE
                ):
                    item.update(
                        status="blocked",
                        block_reason="Prior writer may still be active; reconcile it or select an isolated workspace before unblocking.",
                    )

        return self.mutate(plan["revision"], record)

    def reuse(self, item_id: str, reference: str, expected: int) -> dict:
        """Explicitly accept verified evidence from this plan's adopted execution."""
        evidence(reference)

        def change(plan):
            origin = plan.get("adopted_from")
            require(
                isinstance(origin, dict)
                and isinstance(origin.get("path"), str)
                and bool(origin["path"]),
                "reuse_unavailable",
                "Reuse requires an explicitly adopted source plan.",
            )
            archive = self.root / "previous-plan.json"
            require(
                archive.is_file() and not archive.is_symlink(),
                "reuse_unavailable",
                "The adopted plan snapshot must remain accessible.",
            )
            previous = read_json(archive)
            require(
                isinstance(previous, dict)
                and isinstance(previous.get("items"), dict)
                and isinstance(previous.get("attempts"), dict)
                and previous.get("plan_id") == origin.get("plan_id")
                and previous.get("run_id") == origin.get("run_id")
                and plan["plan_id"] == previous.get("plan_id"),
                "reuse_source_invalid",
                "The archived plan does not match the recorded adoption.",
            )
            require(item_id in plan["items"], "unknown_item", item_id)
            item = plan["items"][item_id]
            require(
                item["status"] == "ready" and ready(plan, item),
                "item_not_ready",
                "Current approval and dependencies must permit this item.",
            )
            old = previous.get("items", {}).get(item_id)
            require(
                isinstance(old, dict)
                and old.get("status") == "accepted"
                and not old.get("adopted_evidence"),
                "reuse_source_invalid",
                "Reuse needs directly accepted source work; chained reuse is unsupported.",
            )
            contract_fields = (
                "title",
                "acceptance",
                "delivery",
                "depends_on",
                "write_scope",
                "resources",
                "access",
            )
            require(
                all(item.get(field) == old.get(field) for field in contract_fields),
                "reuse_contract_changed",
                "The task contract changed; review it through a new attempt.",
            )
            attempt = previous.get("attempts", {}).get(old.get("active_attempt"))
            require(
                isinstance(attempt, dict)
                and attempt.get("attempt_id") == old.get("active_attempt")
                and attempt.get("item_id") == item_id
                and attempt.get("run_id") == previous["run_id"]
                and attempt.get("status") == "reviewed"
                and attempt.get("verdict") == "accepted"
                and isinstance(attempt.get("directory"), str)
                and bool(attempt["directory"])
                and isinstance(attempt.get("observation"), dict),
                "reuse_source_invalid",
                "The source item must have a manager-reviewed accepted attempt.",
            )
            require(
                attempt.get("observation", {}).get("state") == "exited",
                "exit_unconfirmed",
                "The original writer's exit must be confirmed before reuse.",
            )
            directory = Path(attempt["directory"])
            expected_directory = (
                Path(origin["path"]) / "attempts" / item_id / attempt["attempt_id"]
            )
            require(
                directory.resolve() == expected_directory.resolve(),
                "reuse_source_invalid",
                "Source artifacts must belong to the adopted attempt.",
            )
            results = self._verified_results(previous, attempt)
            require(
                any(result.get("outcome") == "candidate_ready" for result in results)
                and all(
                    result.get("outcome") in {"candidate_ready", "candidate"}
                    for result in results
                ),
                "result_not_ready",
                "Reuse requires an intact successful task report and no executor failure.",
            )
            proofs = []
            for result_name, event_name in (
                ("result.json", "candidate-event.json"),
                ("executor-result.json", "completion.json"),
            ):
                result_path, event_path = (
                    directory / result_name,
                    directory / event_name,
                )
                if result_path.exists():
                    event = read_json(event_path)
                    proofs.append(
                        {
                            "result_path": str(result_path),
                            "result_sha256": hashlib.sha256(
                                result_path.read_bytes()
                            ).hexdigest(),
                            "published_result_digest": event["result_digest"],
                            "event_path": str(event_path),
                            "event_sha256": hashlib.sha256(
                                event_path.read_bytes()
                            ).hexdigest(),
                        }
                    )
            item.update(
                status="accepted",
                summary=reference,
                adopted_evidence={
                    "plan_id": previous["plan_id"],
                    "run_id": previous["run_id"],
                    "item_id": item_id,
                    "attempt_id": attempt["attempt_id"],
                    "source_plan": str(Path(origin["path"]) / "plan.json"),
                    "snapshot_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "source_review": attempt.get("review_evidence"),
                    "results": proofs,
                    "reference": reference,
                    "observed_at": utc_now(),
                },
            )

        return self.mutate(expected, change)
