from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wuditask import agent_plan
from wuditask.agent_plan import PlanStore, check_rights
from wuditask.cli import main
from wuditask.errors import WudiTaskError


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.registry = self.base / "reservations"
        self.spec = {
            "goal": "Deliver the complete local change",
            "task_ref": "local:recovery-regression",
            "agreement": {
                "scope": "The isolated workspace",
                "acceptance": "Fixed-input regression evidence passes",
                "approval_ref": "user authorized this exact scope",
            },
            "deliveries": {"change": {"title": "Complete local change"}},
            "items": {
                "implementation": {
                    "title": "Implement and verify the change",
                    "delivery": "change",
                    "acceptance": "Review actual artifacts and regression evidence",
                    "write_scope": ["src"],
                    "depends_on": [],
                }
            },
            "decisions": {},
        }
        self.prompt = "Implement the authorized change and return candidate evidence."
        self.preflight = {
            "decision": "proceed",
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "main_evidence": "Fixed main SHA inspected",
            "open_pr_disposition": "Complete open/draft inventory has no overlap",
            "observed_at": "2026-10-24T12:00:00Z",
        }

    def store(self, name="plan"):
        store = PlanStore(self.base / name, registry=self.registry)
        store.init(deepcopy(self.spec), local_reason="Isolated maintenance regression")
        return store

    def prepare(self, store, attempt="first", backend="codex-native"):
        return store.prepare(
            "implementation", attempt, backend, self.workspace, self.prompt,
            self.preflight, store.load()["revision"],
        )

    def report(self, store, attempt="first"):
        plan = store.load()
        return {
            "plan_id": plan["plan_id"],
            "run_id": plan["run_id"],
            "item_id": "implementation",
            "attempt_id": attempt,
            "outcome": "candidate_ready",
            "summary": "Candidate artifact and verification are available",
        }

    def assert_code(self, code, operation, *args, **kwargs):
        with self.assertRaises(WudiTaskError) as raised:
            operation(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)

    def mock_authority(self, ticket):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("wuditask.execution_cli.authenticated_actor", return_value={}))
        stack.enter_context(patch("wuditask.execution_cli._load_config", return_value={}))
        stack.enter_context(patch("wuditask.execution_cli._load_session", return_value=ticket))
        stack.enter_context(patch("wuditask.execution_cli.coordinator"))
        return stack.enter_context(patch("wuditask.execution_cli._check"))

    def test_other_task_claim_is_rejected_before_checking_current_run(self):
        ticket = {"run_id": "shared-run", "url": "https://github.com/example/repo/issues/1"}
        checked = self.mock_authority(ticket)
        plan = {"mode": "coordinated", "session": str(self.base / "session.json"),
                "run_id": "shared-run", "task_ref": "https://github.com/example/repo/issues/2"}
        self.assert_code("plan_task_mismatch", check_rights, plan)
        checked.assert_not_called()
        plan["task_ref"] = "https://github.com/example/repo/pull/1"
        self.assert_code("plan_task_mismatch", check_rights, plan)
        checked.assert_not_called()

    def test_same_task_normalization_preserves_remote_rights_check(self):
        ticket = {"run_id": "shared-run", "url": "https://github.com/example/repo/issues/1"}
        checked = self.mock_authority(ticket)
        plan = {"mode": "coordinated", "session": str(self.base / "session.json"),
                "run_id": "shared-run", "task_ref": "https://github.com/Example/Repo/issues/1/"}
        check_rights(plan)
        checked.assert_called_once()
        checked.reset_mock()
        plan["run_id"] = "superseded-run"
        self.assert_code("plan_run_mismatch", check_rights, plan)
        checked.assert_not_called()

    def test_wrong_task_binding_does_not_create_a_plan(self):
        ticket = {"run_id": "shared-run", "url": "https://github.com/example/repo/issues/1"}
        self.mock_authority(ticket)
        session = self.base / "session.json"
        session.write_text(json.dumps(ticket))
        spec = deepcopy(self.spec)
        spec["task_ref"] = "https://github.com/example/repo/issues/2"
        store = PlanStore(self.base / "wrong-task", registry=self.registry)
        self.assert_code("plan_task_mismatch", store.init, spec, session=session)
        self.assertFalse((store.root / "plan.json").exists())

    def test_failed_plan_save_recovers_unknown_attempt_without_starting_worker(self):
        store = self.store()
        before = store.load()
        with patch.object(store, "_save", side_effect=OSError("injected plan save failure")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.prepare(store)
        self.assertEqual(before, store.load())
        assignment = store.root / "attempts" / "implementation" / "first" / "assignment.json"
        assignment_bytes = assignment.read_bytes()
        rival = self.store("rival")
        self.assert_code("work_scope_busy", self.prepare, rival)
        with patch("wuditask.agent_executor.launch") as launched:
            recovered = store.reconcile()["plan"]
            launched.assert_not_called()
        attempt = recovered["attempts"]["first"]
        self.assertEqual("launch_unknown", attempt["status"])
        self.assertEqual("first", recovered["items"]["implementation"]["active_attempt"])
        self.assertEqual(before["run_id"], attempt["run_id"])
        self.assertEqual(assignment_bytes, assignment.read_bytes())
        self.assert_code("work_scope_busy", self.prepare, rival)
        self.assert_code("exit_unconfirmed", store.review, "first", "needs_rework",
                         "Unknown invocation requires reconciliation", recovered["revision"])
        store.observe("first", "not_started", "Host confirmed no native invocation exists",
                      store.load()["revision"])
        store.review("first", "needs_rework", "Retire interrupted preparation",
                     store.load()["revision"])
        self.prepare(rival)
        store.reconcile()
        self.assertEqual("reviewed", store.load()["attempts"]["first"]["status"])

    def test_assignment_write_failure_recovers_reservation_identity(self):
        store = self.store()
        writer = agent_plan.journal_write

        def fail_assignment(path, value):
            if path.name == "assignment.json":
                raise OSError("injected assignment failure")
            return writer(path, value)

        with patch("wuditask.agent_plan.journal_write", side_effect=fail_assignment):
            with self.assertRaisesRegex(OSError, "injected"):
                self.prepare(store, backend="claude")
        self.assertEqual({}, store.load()["attempts"])
        recovered = store.reconcile()["plan"]
        self.assertEqual("launch_unknown", recovered["attempts"]["first"]["status"])
        with patch("wuditask.agent_executor.launch") as launched:
            observed = store.reconcile()["plan"]
            launched.assert_not_called()
        self.assertEqual("not_started", observed["attempts"]["first"]["observation"]["state"])
        store.review("first", "needs_rework", "Executor confirms it never started",
                     store.load()["revision"])
        self.prepare(store, attempt="retry", backend="claude")
        self.assertEqual("retry", store.load()["items"]["implementation"]["active_attempt"])

    def test_late_first_report_is_historical_after_terminal_review(self):
        store = self.store()
        self.prepare(store)
        result = self.report(store)
        store.observe("first", "exited", "Host confirmed exact invocation exited",
                      store.load()["revision"])
        store.review("first", "blocked", "Exited without required report",
                     store.load()["revision"])
        reviewed = store.load()
        store.report("first", result)
        polled = store.poll()
        self.assertEqual([], polled["pending_events"])
        self.assertEqual(1, len(polled["historical_events"]))
        self.assertFalse(polled["historical_events"][0]["current"])
        self.assertEqual(reviewed, store.load())
        store.report("first", result)
        self.assertEqual(1, len(store.poll()["historical_events"]))
        self.assert_code("attempt_terminal", store.review, "first", "accepted",
                         "A late report cannot reopen a review", store.load()["revision"])

    def test_previous_run_event_is_historical_and_cannot_accept_new_run(self):
        store = self.store()
        self.prepare(store)
        store.report("first", self.report(store))
        self.assertEqual(1, len(store.poll()["pending_events"]))
        store.mutate(store.load()["revision"], lambda plan: plan.update(run_id="new-run"))
        polled = store.poll()
        self.assertEqual([], polled["pending_events"])
        self.assertFalse(polled["historical_events"][0]["current"])
        self.assert_code("stale_attempt", store.review, "first", "accepted",
                         "Old authority cannot accept", store.load()["revision"])

    def cli(self, *arguments, code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("wuditask.agent_plan.personal_root", return_value=self.base / "personal"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            actual = main(["--json", "work", *map(str, arguments)])
        self.assertEqual(code, actual, stdout.getvalue() + stderr.getvalue())
        return json.loads(stdout.getvalue() or stderr.getvalue())

    def test_cli_native_roundtrip_and_stale_revision_error(self):
        plan = self.base / "cli-plan"
        spec = self.base / "spec.json"
        spec.write_text(json.dumps(self.spec))
        initial = self.cli("init", "--plan", plan, "--spec", spec,
                           "--local-reason", "Isolated CLI regression")["work"]
        self.assertTrue((plan / "board.html").is_file())
        prompt = self.base / "prompt.md"
        prompt.write_text(self.prompt)
        preflight = self.base / "preflight.json"
        preflight.write_text(json.dumps(self.preflight))
        prepared = self.cli("prepare", "--plan", plan, "--revision", initial["revision"],
                            "--item", "implementation", "--attempt", "native-1",
                            "--backend", "codex-native", "--workspace", self.workspace,
                            "--prompt", prompt, "--preflight", preflight)["work"]
        error = self.cli("observe", "--plan", plan, "--revision", initial["revision"],
                         "--attempt", "native-1", "--state", "exited", "--reference",
                         "Stale observation must not win", code=3)
        self.assertEqual("stale_plan_revision", error["error"]["code"])
        report = self.base / "result.json"
        report.write_text(json.dumps({"plan_id": prepared["plan_id"], "run_id": prepared["run_id"],
                                      "item_id": "implementation", "attempt_id": "native-1",
                                      "outcome": "candidate_ready", "summary": "Fixed-input checks passed"}))
        self.cli("report", "--plan", plan, "--attempt", "native-1", "--result", report)
        observed = self.cli("observe", "--plan", plan, "--revision", prepared["revision"],
                            "--attempt", "native-1", "--state", "exited", "--handle", "native-handle",
                            "--reference", "Host confirmed exact invocation exit")["work"]
        reviewed = self.cli("review", "--plan", plan, "--revision", observed["revision"],
                            "--attempt", "native-1", "--verdict", "accepted",
                            "--reference", "Inspected artifact and regression evidence")["work"]
        self.assertEqual("accepted", reviewed["items"]["implementation"]["status"])
        self.assertEqual([], self.cli("poll", "--plan", plan)["work"]["pending_events"])
        self.assertIn("accepted", (plan / "board.html").read_text())


if __name__ == "__main__":
    unittest.main()
