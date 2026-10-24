from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from wuditask.agent_plan import PlanStore
from wuditask.errors import WudiTaskError
from wuditask.util import utc_now


class AgentPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.registry = self.base / "reservations"
        self.spec = {
            "goal": "Implement a bounded local change",
            "task_ref": "local:executor-tests",
            "agreement": {
                "scope": "Only the registered workspace",
                "acceptance": "Reviewed outputs and current-head checks pass",
                "approval_ref": "user:approved-scope",
            },
            "deliveries": {"delivery": {"title": "Reviewed change"}},
            "items": {"first": self.item()},
            "decisions": {},
        }

    @staticmethod
    def item(**fields):
        return {
            "title": "Implement the change",
            "delivery": "delivery",
            "acceptance": "The specified behavior is verified",
            "write_scope": ["src"],
            **fields,
        }

    def store(self, spec=None):
        store = PlanStore(
            self.base / uuid.uuid4().hex,
            registry=self.registry,
            checker=lambda plan: None,
        )
        store.init(deepcopy(spec or self.spec), local_reason="isolated unit test")
        return store

    def rev(self, store):
        return store.load()["revision"]

    def prepare(
        self,
        store,
        item="first",
        attempt="attempt",
        workspace=None,
        backend="codex-native",
        continue_attempt=None,
    ):
        prompt = "Implement the accepted task contract and return candidate evidence."
        preflight = {
            "decision": "proceed",
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "main_evidence": "main:abc123 checked against source",
            "open_pr_disposition": "No overlapping open or draft PR",
            "observed_at": utc_now(),
        }
        return store.prepare(
            item,
            attempt,
            backend,
            workspace or self.workspace,
            prompt,
            preflight,
            self.rev(store),
            continue_attempt,
        )

    def result(self, store, attempt="attempt", **fields):
        plan = store.load()
        current = plan["attempts"][attempt]
        return {
            "plan_id": plan["plan_id"],
            "run_id": current["run_id"],
            "item_id": current["item_id"],
            "attempt_id": attempt,
            "outcome": "candidate_ready",
            "summary": "Changed source; focused tests passed",
            **fields,
        }

    def directory(self, store, attempt="attempt"):
        return Path(store.load()["attempts"][attempt]["directory"])

    def exited(self, store, attempt="attempt"):
        return store.observe(
            attempt, "exited", "host confirms exact agent exited", self.rev(store)
        )

    def accept(self, store, item="first", attempt="attempt"):
        self.prepare(store, item, attempt)
        store.report(attempt, self.result(store, attempt))
        self.exited(store, attempt)
        return store.review(
            attempt,
            "accepted",
            "reviewed diff and focused test evidence",
            self.rev(store),
        )

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(WudiTaskError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_dag_rejects_cycles_and_unknown_dependencies(self):
        for dependencies, code in [
            ([{"kind": "artifact", "ref": "first"}], "plan_cycle"),
            ([{"kind": "artifact", "ref": "missing"}], "unknown_dependency"),
            ([{"kind": "invented", "ref": "first"}], "unknown_dependency"),
        ]:
            with self.subTest(code=code, dependencies=dependencies):
                spec = deepcopy(self.spec)
                spec["items"]["first"]["depends_on"] = dependencies
                self.assert_error(code, self.store, spec)
        spec = deepcopy(self.spec)
        spec["items"]["first"]["depends_on"] = [{"kind": "artifact", "ref": "second"}]
        spec["items"]["second"] = self.item(
            depends_on=[{"kind": "artifact", "ref": "first"}]
        )
        self.assert_error("plan_cycle", self.store, spec)

    def test_approval_and_artifact_gates_require_explicit_acceptance(self):
        self.spec["agreement"].pop("approval_ref")
        self.spec["items"]["second"] = self.item(
            depends_on=[{"kind": "artifact", "ref": "first"}]
        )
        store = self.store()
        self.assertEqual([], store.poll()["ready_items"])
        self.assert_error("item_not_ready", self.prepare, store)
        store.approve("user approved exact contract", self.rev(store))
        self.prepare(store)
        before = store.load()
        store.report("attempt", self.result(store))
        self.assertEqual(before, store.load())
        self.assertEqual("planned", store.load()["items"]["second"]["status"])
        self.exited(store)
        plan = store.review(
            "attempt", "accepted", "verified candidate", self.rev(store)
        )
        self.assertEqual("ready", plan["items"]["second"]["status"])
        self.assertEqual("implementing", plan["deliveries"]["delivery"]["status"])

    def test_stale_revision_cannot_overwrite_newer_contract(self):
        store = self.store()
        revision = self.rev(store)
        store.edit_item("first", {"title": "A revised task"}, revision)
        self.assert_error(
            "stale_plan_revision",
            store.edit_item,
            "first",
            {"title": "Lost update"},
            revision,
        )
        self.assertEqual("A revised task", store.load()["items"]["first"]["title"])

    def test_candidate_does_not_release_writer_until_exit_is_confirmed(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.assert_error(
            "exit_unconfirmed",
            store.review,
            "attempt",
            "accepted",
            "review",
            self.rev(store),
        )
        rival = self.store()
        self.assert_error("work_scope_busy", self.prepare, rival)
        self.exited(store)
        store.review(
            "attempt", "accepted", "review after exact process exit", self.rev(store)
        )
        self.prepare(rival)

    def test_unknown_process_observation_keeps_reservation(self):
        store = self.store()
        self.prepare(store)
        store.observe(
            "attempt", "unknown", "host response unavailable", self.rev(store)
        )
        self.assert_error(
            "exit_unconfirmed",
            store.review,
            "attempt",
            "needs_rework",
            "requires reconciliation",
            self.rev(store),
        )
        self.assert_error("work_scope_busy", self.prepare, self.store())

    def test_nested_write_scopes_conflict_across_plans(self):
        store = self.store()
        self.prepare(store)
        spec = deepcopy(self.spec)
        spec["items"]["first"]["write_scope"] = ["src/deeper"]
        self.assert_error("work_scope_busy", self.prepare, self.store(spec))
        spec["items"]["first"]["write_scope"] = ["docs"]
        self.prepare(self.store(spec))

    def test_readers_can_share_scope_but_resource_claims_are_exclusive(self):
        self.spec["items"]["first"]["access"] = "read"
        self.prepare(self.store())
        self.prepare(self.store())
        spec = deepcopy(self.spec)
        spec["items"]["first"].update(resources=["device:0"], write_scope=["other"])
        self.prepare(self.store(spec))
        elsewhere = self.base / "other-workspace"
        elsewhere.mkdir()
        self.assert_error(
            "work_scope_busy", self.prepare, self.store(spec), workspace=elsewhere
        )

    def test_repeated_prepare_is_idempotent_and_changed_assignment_collides(self):
        store = self.store()
        first = self.prepare(store)
        self.assertEqual(first, self.prepare(store))
        other = self.base / "other"
        other.mkdir()
        self.assert_error("attempt_collision", self.prepare, store, workspace=other)

    def test_decision_change_invalidates_transitive_accepted_results(self):
        self.spec["decisions"] = {"design": {"question": "Which interface?"}}
        self.spec["items"]["first"]["depends_on"] = [
            {"kind": "decision", "ref": "design"}
        ]
        self.spec["items"]["second"] = self.item(
            depends_on=[{"kind": "artifact", "ref": "first"}]
        )
        store = self.store()
        self.assert_error("item_not_ready", self.prepare, store)
        store.decision(
            "design", "approved", "user chose documented interface", self.rev(store)
        )
        self.accept(store)
        self.accept(store, "second", "second-attempt")
        plan = store.decision(
            "design", "rejected", "requirements changed", self.rev(store)
        )
        self.assertEqual("planned", plan["items"]["first"]["status"])
        self.assertEqual("planned", plan["items"]["second"]["status"])
        self.assertEqual("blocked_or_validation_needed", plan["summary_state"])

    def test_decision_cannot_change_under_an_active_affected_writer(self):
        self.spec["decisions"] = {"design": {"question": "Which interface?"}}
        self.spec["items"]["first"]["depends_on"] = [
            {"kind": "decision", "ref": "design"}
        ]
        store = self.store()
        store.decision("design", "approved", "approved interface", self.rev(store))
        self.prepare(store)
        self.assert_error(
            "affected_attempt_active",
            store.decision,
            "design",
            "pending",
            "reconsider",
            self.rev(store),
        )
        self.assertEqual("approved", store.load()["decisions"]["design"]["status"])

    def test_delivery_readiness_requires_passes_for_exact_head(self):
        store = self.store()
        self.accept(store)
        passed = {
            "head": "head-a",
            "status": "passed",
            "reference": "check:1",
            "observed_at": utc_now(),
        }
        update = {
            "head": "head-a",
            "status": "ready_for_merge",
            "review_evidence": passed,
            "ci_evidence": {**passed, "head": "old-head"},
        }
        self.assert_error(
            "delivery_evidence_stale",
            store.delivery,
            "delivery",
            update,
            self.rev(store),
        )
        update["ci_evidence"] = passed
        plan = store.delivery("delivery", update, self.rev(store))
        self.assertEqual("ready_for_merge", plan["deliveries"]["delivery"]["status"])
        plan = store.delivery("delivery", {"head": "head-b"}, self.rev(store))
        delivery = plan["deliveries"]["delivery"]
        self.assertEqual("validating", delivery["status"])
        self.assertNotIn("review_evidence", delivery)
        self.assertNotIn("ci_evidence", delivery)
        self.assert_error(
            "delivery_evidence_stale",
            store.delivery,
            "delivery",
            {"status": "ready_for_merge"},
            self.rev(store),
        )

    def test_merge_gate_waits_for_verified_matching_baseline(self):
        self.spec["deliveries"]["downstream"] = {"title": "Downstream integration"}
        self.spec["items"]["second"] = self.item(
            delivery="downstream",
            depends_on=[
                {"kind": "merged", "ref": "delivery", "merge_commit": "merge-a"}
            ],
        )
        store = self.store()
        self.accept(store)
        merge = {
            "head": "head-a",
            "merge_commit": "merge-a",
            "reference": "remote merged PR",
        }
        store.delivery(
            "delivery",
            {"head": "head-a", "status": "merged", "merge_evidence": merge},
            self.rev(store),
        )
        self.assertEqual("planned", store.load()["items"]["second"]["status"])
        baseline = {
            "merge_commit": "other-merge",
            "status": "passed",
            "reference": "baseline tests",
        }
        self.assert_error(
            "baseline_unverified",
            store.delivery,
            "delivery",
            {"status": "baseline_verified", "baseline_evidence": baseline},
            self.rev(store),
        )
        baseline["merge_commit"] = "merge-a"
        plan = store.delivery(
            "delivery",
            {"status": "baseline_verified", "baseline_evidence": baseline},
            self.rev(store),
        )
        self.assertEqual("ready", plan["items"]["second"]["status"])

    def test_worker_cannot_report_accepted_or_different_attempt_identity(self):
        store = self.store()
        self.prepare(store)
        for fields in [
            dict(outcome="accepted"),
            dict(attempt_id="other"),
            dict(run_id="old-run"),
            dict(item_id="other"),
        ]:
            with self.subTest(fields=fields), self.assertRaises(WudiTaskError):
                store.report("attempt", self.result(store, **fields))
        self.assertFalse((self.directory(store) / "result.json").exists())

    def test_first_result_is_immutable_and_repeated_report_is_idempotent(self):
        store = self.store()
        self.prepare(store)
        result = self.result(store)
        first = store.report("attempt", result)
        self.assertEqual(first, store.report("attempt", result))
        self.assert_error(
            "result_conflict",
            store.report,
            "attempt",
            {**result, "summary": "changed claim"},
        )

    def test_old_attempt_cannot_retire_or_accept_new_writer(self):
        store = self.store()
        self.prepare(store)
        result = self.result(store)
        store.report("attempt", result)
        self.exited(store)
        store.review(
            "attempt", "needs_rework", "missing a required case", self.rev(store)
        )
        self.prepare(store, attempt="retry")
        store.report("attempt", result)
        self.assert_error(
            "stale_attempt",
            store.review,
            "attempt",
            "accepted",
            "late result",
            self.rev(store),
        )
        self.assertEqual("retry", store.load()["items"]["first"]["active_attempt"])
        self.assertEqual("running", store.load()["items"]["first"]["status"])

    def test_previous_run_candidate_cannot_update_current_plan(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        store.mutate(
            self.rev(store), lambda plan: plan.update(run_id="replacement-run")
        )
        self.assertFalse(store.poll()["historical_events"][0]["current"])
        self.assert_error(
            "stale_attempt",
            store.review,
            "attempt",
            "accepted",
            "old execution",
            self.rev(store),
        )

    def test_tampered_candidate_digest_cannot_be_accepted(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        path = self.directory(store) / "result.json"
        result = json.loads(path.read_text())
        result["summary"] = "unreviewed replacement evidence"
        path.write_text(json.dumps(result))
        with self.assertRaises(WudiTaskError):
            store.review("attempt", "accepted", "review", self.rev(store))
        self.assertNotEqual("accepted", store.load()["items"]["first"]["status"])

    def test_repeated_report_cannot_rebind_event_to_tampered_result(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        directory = self.directory(store)
        event_before = (directory / "candidate-event.json").read_bytes()
        changed = self.result(store, summary="changed evidence after publication")
        (directory / "result.json").write_text(json.dumps(changed))
        with self.assertRaises(WudiTaskError):
            store.report("attempt", changed)
        self.assertEqual(
            event_before, (directory / "candidate-event.json").read_bytes()
        )

    def test_malformed_result_cannot_be_accepted(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        (self.directory(store) / "result.json").write_text("{incomplete")
        with self.assertRaises(WudiTaskError):
            store.review("attempt", "accepted", "review", self.rev(store))

    def test_unstarted_attempt_cannot_be_accepted(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        store.observe(
            "attempt", "not_started", "executor never started", self.rev(store)
        )
        self.assert_error(
            "exit_unconfirmed",
            store.review,
            "attempt",
            "accepted",
            "candidate claim",
            self.rev(store),
        )

    def test_executor_failure_vetoes_child_success_claim(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        directory = self.directory(store)
        failed = self.result(
            store, outcome="executor_failed", accepted=False, returncode=1
        )
        result_path = directory / "executor-result.json"
        result_path.write_text(json.dumps(failed))
        event = {
            "plan_id": store.load()["plan_id"],
            "attempt_id": "attempt",
            "run_id": failed["run_id"],
            "item_id": "first",
            "result_path": "executor-result.json",
            "result_digest": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "event_seq": 1,
            "outcome": "executor_failed",
        }
        (directory / "completion.json").write_text(json.dumps(event))
        self.assert_error(
            "result_not_ready",
            store.review,
            "attempt",
            "accepted",
            "child claimed success",
            self.rev(store),
        )

    def test_nonobject_result_is_a_validation_error(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        (self.directory(store) / "result.json").write_text("[]")
        with self.assertRaises(WudiTaskError):
            store.review("attempt", "accepted", "review", self.rev(store))

    def test_result_symlink_cannot_reference_external_file(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        result_path = self.directory(store) / "result.json"
        outside = self.base / "external-result.json"
        outside.write_bytes(result_path.read_bytes())
        result_path.unlink()
        result_path.symlink_to(outside)
        with self.assertRaises(WudiTaskError):
            store.review("attempt", "accepted", "review", self.rev(store))

    def test_adoption_blocks_old_writer_and_requires_separate_scope(self):
        original = self.store()
        self.prepare(original)
        adopted = PlanStore(
            self.base / "adopted", registry=self.registry, checker=lambda plan: None
        )
        plan = adopted.adopt(
            original.root,
            "verified takeover context",
            local_reason="local test continuation",
        )
        self.assertEqual(original.load()["plan_id"], plan["plan_id"])
        self.assertNotEqual(original.load()["run_id"], plan["run_id"])
        self.assertEqual({}, plan["attempts"])
        self.assertEqual("blocked", plan["items"]["first"]["status"])
        adopted.approve("user authorizes continuing scope", self.rev(adopted))
        self.assert_error("item_not_ready", self.prepare, adopted)
        adopted.edit_item("first", {"status": "planned"}, self.rev(adopted))
        self.assert_error("work_scope_busy", self.prepare, adopted)
        isolated = self.base / "isolated"
        isolated.mkdir()
        self.prepare(adopted, workspace=isolated)
        self.assertEqual("prepared", original.load()["attempts"]["attempt"]["status"])

    def test_adoption_preserves_evidence_without_auto_acceptance(self):
        original = self.store()
        self.accept(original)
        adopted = PlanStore(
            self.base / "adopted", registry=self.registry, checker=lambda plan: None
        )
        plan = adopted.adopt(
            original.root, "review prior evidence", local_reason="continue local work"
        )
        self.assertNotEqual("accepted", plan["items"]["first"]["status"])
        self.assertFalse(plan["agreement"].get("approval_ref"))
        self.assertEqual([], adopted.poll()["pending_events"])
        archived = json.loads((adopted.root / "previous-plan.json").read_text())
        self.assertEqual("accepted", archived["items"]["first"]["status"])

    def test_launch_rejects_changed_prompt_before_any_process(self):
        store = self.store()
        self.prepare(store, backend="claude")
        (self.directory(store) / "prompt.md").write_text(
            "Changed instructions after preflight"
        )
        with patch("wuditask.agent_executor.launch") as launch:
            self.assert_error("prompt_modified", store.launch, "attempt")
        launch.assert_not_called()

    def test_same_branch_in_different_git_worktrees_conflicts(self):
        repository = self.base / "repository"
        linked = self.base / "linked"
        subprocess.run(
            ["git", "init", "-b", "shared", str(repository)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "baseline",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "add",
                "--force",
                str(linked),
                "shared",
            ],
            check=True,
            capture_output=True,
        )
        self.prepare(self.store(), workspace=repository)
        self.assert_error(
            "work_scope_busy", self.prepare, self.store(), workspace=linked
        )
        subprocess.run(
            ["git", "-C", str(linked), "checkout", "-b", "independent"],
            check=True,
            capture_output=True,
        )
        self.prepare(self.store(), workspace=linked)

    def test_semantic_cycle_through_merged_delivery_is_rejected(self):
        own_delivery = deepcopy(self.spec)
        own_delivery["items"]["first"]["depends_on"] = [
            {"kind": "merged", "ref": "delivery"}
        ]
        self.assert_error("plan_cycle", self.store, own_delivery)
        mixed = deepcopy(self.spec)
        mixed["deliveries"]["second-delivery"] = {"title": "second delivery"}
        mixed["items"]["first"]["depends_on"] = [
            {"kind": "merged", "ref": "second-delivery"}
        ]
        mixed["items"]["second"] = self.item(
            delivery="second-delivery",
            depends_on=[{"kind": "artifact", "ref": "first"}],
        )
        self.assert_error("plan_cycle", self.store, mixed)

    def test_contract_edit_cannot_rewrite_verified_delivery_history(self):
        self.spec["deliveries"]["downstream"] = {"title": "integration"}
        self.spec["items"]["second"] = self.item(
            delivery="downstream", depends_on=[{"kind": "merged", "ref": "delivery"}]
        )
        store = self.store()
        self.accept(store)
        store.delivery(
            "delivery",
            {
                "head": "head",
                "status": "baseline_verified",
                "merge_evidence": {
                    "head": "head",
                    "merge_commit": "merge",
                    "reference": "verified remote merge",
                },
                "baseline_evidence": {
                    "merge_commit": "merge",
                    "status": "passed",
                    "reference": "baseline test",
                },
            },
            self.rev(store),
        )
        self.assertEqual("ready", store.load()["items"]["second"]["status"])
        before = store.load()
        with self.assertRaises(WudiTaskError):
            store.edit_item(
                "first",
                {"acceptance": "An additional required behavior"},
                self.rev(store),
            )
        self.assertEqual(before, store.load())

    def test_continuation_keeps_author_session_and_native_handle(self):
        store = self.store()
        self.prepare(store)
        store.observe(
            "attempt",
            "exited",
            "host confirms exit",
            self.rev(store),
            handle="native-author",
        )
        store.review(
            "attempt", "needs_rework", "address review feedback", self.rev(store)
        )
        plan = self.prepare(store, attempt="followup", continue_attempt="attempt")
        original, followup = plan["attempts"]["attempt"], plan["attempts"]["followup"]
        self.assertEqual(original["session_id"], followup["session_id"])
        self.assertEqual("native-author", followup["previous_handle"])
        self.assertEqual("attempt", followup["continue_attempt"])
        with self.assertRaises(WudiTaskError):
            store.observe(
                "followup",
                "running",
                "new writer",
                self.rev(store),
                handle="different-author",
            )
        store.observe(
            "followup",
            "running",
            "same writer resumed",
            self.rev(store),
            handle="native-author",
        )

    def test_continuation_cannot_switch_workspace_or_backend(self):
        store = self.store()
        self.prepare(store)
        self.exited(store)
        store.review("attempt", "needs_rework", "address feedback", self.rev(store))
        isolated = self.base / "isolated"
        isolated.mkdir()
        self.assert_error(
            "continuation_invalid",
            self.prepare,
            store,
            attempt="followup",
            continue_attempt="attempt",
            workspace=isolated,
        )
        self.assert_error(
            "continuation_invalid",
            self.prepare,
            store,
            attempt="followup",
            continue_attempt="attempt",
            backend="claude",
        )

    def test_claude_continuation_launch_passes_resume_to_executor(self):
        store = self.store()
        self.prepare(store, backend="claude")
        with patch(
            "wuditask.agent_executor.inspect", return_value={"status": "exited"}
        ):
            self.exited(store)
            store.review("attempt", "needs_rework", "address feedback", self.rev(store))
        plan = self.prepare(
            store, attempt="followup", backend="claude", continue_attempt="attempt"
        )
        with patch(
            "wuditask.agent_executor.launch", return_value={"status": "starting"}
        ) as launch:
            store.launch("followup")
        self.assertTrue(launch.call_args.kwargs["resume"])
        self.assertEqual(
            plan["attempts"]["attempt"]["session_id"], launch.call_args.args[4]
        )

    def test_session_reservation_blocks_parallel_reader_continuations(self):
        self.spec["items"]["first"]["access"] = "read"
        first, second = self.store(), self.store()
        fixed = uuid.UUID("00000000-0000-4000-8000-000000000001")
        with patch("wuditask.agent_plan.uuid.uuid4", return_value=fixed):
            self.prepare(first)
            self.assert_error("work_scope_busy", self.prepare, second)

    def adopt_for_reuse(self, original):
        adopted = PlanStore(
            self.base / uuid.uuid4().hex,
            registry=self.registry,
            checker=lambda plan: None,
        )
        adopted.adopt(
            original.root,
            "reviewed source snapshot",
            local_reason="continue local work",
        )
        adopted.approve("current execution approved", self.rev(adopted))
        return adopted

    def test_explicit_reuse_accepts_source_evidence_and_unlocks_dependency(self):
        self.spec["items"]["second"] = self.item(
            depends_on=[{"kind": "artifact", "ref": "first"}]
        )
        original = self.store()
        self.accept(original)
        original_bytes = (original.root / "plan.json").read_bytes()
        adopted = self.adopt_for_reuse(original)
        self.assertEqual("ready", adopted.load()["items"]["first"]["status"])
        self.assertEqual("planned", adopted.load()["items"]["second"]["status"])
        with patch("wuditask.agent_executor.launch") as launch:
            plan = adopted.reuse(
                "first",
                "rechecked source diff and evidence against current scope",
                self.rev(adopted),
            )
        launch.assert_not_called()
        self.assertEqual("accepted", plan["items"]["first"]["status"])
        self.assertEqual("ready", plan["items"]["second"]["status"])
        self.assertEqual({}, plan["attempts"])
        proof = plan["items"]["first"]["adopted_evidence"]
        self.assertEqual(original.load()["run_id"], proof["run_id"])
        self.assertEqual("attempt", proof["attempt_id"])
        self.assertEqual(original_bytes, (original.root / "plan.json").read_bytes())
        for result in proof["results"]:
            self.assertEqual(
                hashlib.sha256(Path(result["result_path"]).read_bytes()).hexdigest(),
                result["result_sha256"],
            )
            self.assertEqual(
                hashlib.sha256(Path(result["event_path"]).read_bytes()).hexdigest(),
                result["event_sha256"],
            )

    def test_reuse_rejects_missing_or_tampered_source_results(self):
        for fault in ("missing", "tampered"):
            with self.subTest(fault=fault):
                original = self.store()
                self.accept(original)
                adopted = self.adopt_for_reuse(original)
                result_path = self.directory(original) / "result.json"
                if fault == "missing":
                    result_path.unlink()
                else:
                    result = json.loads(result_path.read_text())
                    result["summary"] = "changed source evidence"
                    result_path.write_text(json.dumps(result))
                before = adopted.load()
                with self.assertRaises(WudiTaskError):
                    adopted.reuse("first", "attempted reuse", self.rev(adopted))
                self.assertEqual(before, adopted.load())

    def test_reuse_rejects_source_without_accepted_review(self):
        original = self.store()
        self.prepare(original)
        original.report("attempt", self.result(original))
        self.exited(original)
        original.review(
            "attempt", "needs_rework", "missing behavior", self.rev(original)
        )
        adopted = self.adopt_for_reuse(original)
        self.assert_error(
            "reuse_source_invalid",
            adopted.reuse,
            "first",
            "candidate alone is insufficient",
            self.rev(adopted),
        )

    def test_reuse_requires_adoption_unchanged_contract_and_current_approval(self):
        original = self.store()
        self.assert_error(
            "reuse_unavailable",
            original.reuse,
            "first",
            "no adoption",
            self.rev(original),
        )
        self.accept(original)
        adopted = self.adopt_for_reuse(original)
        adopted.edit_item(
            "first", {"acceptance": "new required behavior"}, self.rev(adopted)
        )
        self.assert_error(
            "reuse_contract_changed",
            adopted.reuse,
            "first",
            "old test cannot prove new contract",
            self.rev(adopted),
        )
        unapproved = PlanStore(
            self.base / "unapproved", registry=self.registry, checker=lambda plan: None
        )
        unapproved.adopt(
            original.root, "saved old context", local_reason="local continuation"
        )
        self.assert_error(
            "item_not_ready",
            unapproved.reuse,
            "first",
            "still needs approval",
            self.rev(unapproved),
        )

    def test_reuse_respects_current_dependency_and_revision_gates(self):
        self.spec["items"]["second"] = self.item(
            depends_on=[{"kind": "artifact", "ref": "first"}]
        )
        original = self.store()
        self.accept(original)
        self.accept(original, "second", "second-attempt")
        adopted = self.adopt_for_reuse(original)
        stale = self.rev(adopted)
        self.assert_error(
            "item_not_ready",
            adopted.reuse,
            "second",
            "upstream not yet reviewed",
            stale,
        )
        adopted.reuse("first", "upstream evidence rechecked", stale)
        self.assert_error(
            "stale_plan_revision", adopted.reuse, "second", "stale manager state", stale
        )
        adopted.reuse("second", "dependent evidence rechecked", self.rev(adopted))
        self.assertEqual("accepted", adopted.load()["items"]["second"]["status"])

    def test_reuse_rejects_missing_snapshot_and_chained_reuse(self):
        original = self.store()
        self.accept(original)
        adopted = self.adopt_for_reuse(original)
        adopted.reuse("first", "verified original evidence", self.rev(adopted))
        chained = self.adopt_for_reuse(adopted)
        self.assert_error(
            "reuse_source_invalid",
            chained.reuse,
            "first",
            "chain is unsupported",
            self.rev(chained),
        )
        (chained.root / "previous-plan.json").unlink()
        self.assert_error(
            "reuse_unavailable",
            chained.reuse,
            "first",
            "snapshot missing",
            self.rev(chained),
        )

    def test_event_path_cannot_escape_attempt_directory(self):
        store = self.store()
        self.prepare(store)
        store.report("attempt", self.result(store))
        self.exited(store)
        path = self.directory(store) / "candidate-event.json"
        event = json.loads(path.read_text())
        event["result_path"] = "../../outside.json"
        path.write_text(json.dumps(event))
        with self.assertRaises(WudiTaskError):
            store.review("attempt", "accepted", "review", self.rev(store))


if __name__ == "__main__":
    unittest.main()
