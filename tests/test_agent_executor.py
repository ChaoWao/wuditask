from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from wuditask import agent_executor as executor


class AgentExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.attempt = self.root / "attempt"
        self.attempt.mkdir()
        self.ids = dict(
            plan_id="plan", run_id="run", item_id="item", attempt_id="attempt"
        )
        (self.attempt / "assignment.json").write_text(json.dumps(self.ids))
        self.prompt = self.attempt / "prompt.md"
        self.prompt.write_text("A fake CLI test; never run a model.")
        self.session = str(uuid.uuid4())
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = patch.dict(
            os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def fake(self, body, backend="claude"):
        executable = self.bin / backend
        executable.write_text(f"#!{sys.executable}\n" + body)
        executable.chmod(0o755)

    def launch(self, **kw):
        return executor.launch(
            self.attempt,
            kw.pop("backend", "claude"),
            self.root,
            self.prompt,
            self.session,
            **kw,
        )

    def wait(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = executor.inspect(self.attempt)
            if state.get("completion") and state.get("wake"):
                return state
            time.sleep(0.01)
        self.fail(f"Supervisor did not finish: {executor.inspect(self.attempt)}")

    def record(self, **fields):
        value = {**self.ids, "cwd": str(self.root), "phase": "running", **fields}
        executor._write(self.attempt / "launch.json", value)
        return value

    def test_detached_success_keeps_child_report_and_publishes_digest(self):
        self.fake(
            "import json, sys\n"
            "assert sys.stdin.read().startswith('A fake CLI')\n"
            "print(json.dumps({'session_id': sys.argv[sys.argv.index('--session-id')+1], 'is_error': False, 'result': 'done'}))\n"
        )
        (self.attempt / "result.json").write_text('{"child": "report"}')
        self.launch()
        state = self.wait()
        self.assertEqual("candidate", state["result"]["outcome"])
        self.assertFalse(state["result"]["accepted"])
        event = state["completion"]
        self.assertEqual(
            executor._digest(self.attempt / event["result_path"]),
            event["result_digest"],
        )
        self.assertEqual(
            "report", json.loads((self.attempt / "result.json").read_text())["child"]
        )
        self.assertTrue((self.attempt / "exit.json").exists())
        self.assertEqual("manual", state["wake"]["status"])
        self.assertEqual("exited", executor.reconcile(self.attempt)["status"])
        self.assertEqual(state["launch"], self.launch())

    def test_failed_authority_check_never_invokes_child(self):
        marker = self.root / "invoked"
        self.fake(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.launch(check_command=[sys.executable, "-c", "raise SystemExit(7)"])
        state = self.wait()
        self.assertFalse(marker.exists())
        self.assertEqual("executor_failed", state["result"]["outcome"])
        self.assertEqual("execution_check_failed", state["result"]["reason"])

    def test_missing_output_and_nonzero_exit_publish_failure(self):
        self.fake("raise SystemExit(9)\n")
        self.launch()
        state = self.wait()
        self.assertEqual("executor_failed", state["result"]["outcome"])
        self.assertEqual(9, state["result"]["returncode"])

    def test_empty_success_is_result_missing(self):
        self.fake("pass\n")
        self.launch()
        self.assertEqual("result_missing", self.wait()["result"]["outcome"])

    def test_concurrent_launch_starts_one_supervisor(self):
        class Process:
            pid = os.getpid()

            def wait(self):
                return 0

        with patch.object(
            executor.subprocess, "Popen", return_value=Process()
        ) as popen:
            with ThreadPoolExecutor(max_workers=8) as pool:
                records = list(pool.map(lambda _: self.launch(), range(16)))
        self.assertEqual(1, popen.call_count)
        self.assertTrue(all(record == records[0] for record in records))

    def test_unknown_start_is_not_retried(self):
        with patch.object(
            executor.subprocess, "Popen", side_effect=RuntimeError("lost response")
        ) as popen:
            self.assertEqual("launch_unknown", self.launch()["status"])
            self.launch()
        self.assertEqual(1, popen.call_count)
        self.assertEqual("unknown", executor.reconcile(self.attempt)["status"])

    def test_post_spawn_failure_preserves_unknown_without_completion(self):
        class Process:
            pid = os.getpid()

            def wait(self):
                return 0

        with (
            patch.object(executor.subprocess, "Popen", return_value=Process()),
            patch.object(
                executor, "_identity", side_effect=OSError("identity unavailable")
            ),
        ):
            self.assertEqual("launch_unknown", self.launch()["status"])
        self.assertFalse((self.attempt / "completion.json").exists())

    def test_known_spawn_failure_publishes_event(self):
        with patch.object(
            executor.subprocess, "Popen", side_effect=FileNotFoundError()
        ):
            self.assertEqual("rejected", self.launch()["status"])
        self.assertEqual(
            "executor_failed", executor.inspect(self.attempt)["completion"]["kind"]
        )

    def test_restart_recovers_result_before_event_without_running_any_command(self):
        record = self.record()
        result = {
            **self.ids,
            "outcome": "candidate",
            "accepted": False,
            "returncode": 0,
        }
        executor._write(self.attempt / "executor-result.json", result)
        with patch.object(executor.subprocess, "Popen") as popen:
            state = executor.reconcile(self.attempt)
        popen.assert_not_called()
        self.assertEqual("candidate", state["completion"]["kind"])
        self.assertEqual(record, state["launch"])

    def test_recovery_rejects_result_for_a_different_attempt(self):
        self.record()
        executor._write(
            self.attempt / "executor-result.json",
            {
                **self.ids,
                "attempt_id": "other",
                "outcome": "candidate",
                "accepted": False,
            },
        )
        with self.assertRaises(executor.WudiTaskError):
            executor.reconcile(self.attempt)
        self.assertFalse((self.attempt / "completion.json").exists())

    def test_manual_wakeup_can_be_explicitly_configured_later(self):
        self.record()
        self.assertEqual("manual", executor.wake(self.attempt, None)["status"])
        self.assertEqual(
            "accepted",
            executor.wake(self.attempt, [sys.executable, "-c", "pass"])["status"],
        )

    def test_dead_supervisor_and_child_produce_result_missing(self):
        dead = {**executor._identity(os.getpid()), "start_ticks": "-1"}
        self.record(supervisor=dead, child=dead)
        event = executor.reconcile(self.attempt)["completion"]
        self.assertEqual("result_missing", event["kind"])
        self.assertEqual(event, executor.reconcile(self.attempt)["completion"])

    def test_living_child_and_unrecorded_spawn_keep_unknown(self):
        dead = {**executor._identity(os.getpid()), "start_ticks": "-1"}
        self.record(supervisor=dead, child=executor._identity(os.getpid()))
        self.assertEqual("running", executor.reconcile(self.attempt)["status"])
        self.record(supervisor=dead, phase="spawning")
        self.assertIsNone(executor.reconcile(self.attempt)["completion"])

    def test_process_identity_detects_reused_pid_and_unknown_fallback(self):
        identity = executor._identity(os.getpid())
        self.assertEqual("running", executor._process_state(identity))
        self.assertEqual(
            "exited", executor._process_state({**identity, "start_ticks": "-1"})
        )
        self.assertEqual("unknown", executor._process_state({"pid": os.getpid()}))

    def test_unknown_wakeup_cannot_be_replayed(self):
        self.record()
        command = ["host-queue", "--thread", "my-thread", "--message", "complete"]
        with patch.object(
            executor.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(command, 30),
        ) as run:
            self.assertEqual("unknown", executor.wake(self.attempt, command)["status"])
            self.assertEqual("unknown", executor.wake(self.attempt, command)["status"])
        self.assertEqual(1, run.call_count)

    def test_wake_argv_is_literal_and_receipt_is_not_task_acceptance(self):
        self.record()
        message = "$(touch should-not-exist); literal"
        command = [sys.executable, "-c", "import sys; print(sys.argv[1])", message]
        receipt = executor.wake(self.attempt, command)
        self.assertEqual("accepted", receipt["status"])
        self.assertEqual(
            message, (self.attempt / "wake.stdout.log").read_text().strip()
        )
        self.assertFalse((self.root / "should-not-exist").exists())
        self.assertFalse((self.attempt / "completion.json").exists())

    def test_claude_errors_and_session_mismatch_are_not_success(self):
        for output in [
            dict(session_id="wrong", is_error=False),
            dict(session_id=self.session, is_error=True),
            dict(session_id=self.session),
        ]:
            self.assertEqual(
                "executor_failed",
                executor._parse_output("claude", json.dumps(output), self.session)[
                    "outcome"
                ],
            )

    def test_codex_jsonl_requires_thread_and_completed_turn(self):
        good = "\n".join(
            map(
                json.dumps,
                [
                    {"type": "thread.started", "thread_id": "actual"},
                    {"type": "turn.completed"},
                ],
            )
        )
        parsed = executor._parse_output("codex", good, self.session)
        self.assertEqual("candidate", parsed["outcome"])
        self.assertEqual("actual", parsed["thread_id"])
        self.assertEqual(
            "result_missing",
            executor._parse_output("codex", '{"type":"turn.completed"}', self.session)[
                "outcome"
            ],
        )
        self.assertEqual(
            "executor_failed",
            executor._parse_output(
                "codex", good + '\n{"type":"turn.failed"}', self.session
            )["outcome"],
        )

    def test_claude_continuation_resumes_the_exact_session(self):
        self.fake(
            "import json, sys\n"
            "assert '--session-id' not in sys.argv\n"
            "assert '--continue' not in sys.argv\n"
            "assert '--fork-session' not in sys.argv\n"
            "session = sys.argv[sys.argv.index('--resume') + 1]\n"
            "print(json.dumps({'session_id':session, 'is_error':False, 'result':'continued'}))\n"
        )
        record = self.launch(resume=True)
        self.assertTrue(record["resume"])
        state = self.wait()
        self.assertEqual("candidate", state["result"]["outcome"])
        self.assertEqual(self.session, state["result"]["session_id"])

    def test_explicit_launcher_path_receives_exact_resume_argv_and_prompt(self):
        wrapper = self.root / "provider wrapper;literal"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"assert sys.argv[1:] == ['-p', '--resume', {self.session!r}, '--output-format', 'json']\n"
            "assert sys.stdin.read().startswith('A fake CLI')\n"
            f"print(json.dumps({{'session_id': {self.session!r}, 'is_error': False, 'result': 'wrapped'}}))\n"
        )
        wrapper.chmod(0o755)
        alias = self.root / "launcher-alias"
        alias.symlink_to(wrapper)
        record = self.launch(resume=True, launcher=str(alias))
        self.assertEqual(str(wrapper.resolve()), record["launcher"])
        self.assertEqual(str(wrapper.resolve()), record["argv"][0])
        self.assertEqual("candidate", self.wait()["result"]["outcome"])

    def test_invalid_launcher_is_rejected_before_any_process(self):
        nonexecutable = self.root / "nonexecutable"
        nonexecutable.write_text("not executable")
        for launcher in [
            str(self.root / "missing"),
            str(nonexecutable),
            str(self.root),
            "claude --profile unsafe",
            "",
        ]:
            with (
                self.subTest(launcher=launcher),
                patch.object(executor.subprocess, "Popen") as popen,
            ):
                with self.assertRaises(executor.WudiTaskError) as caught:
                    self.launch(launcher=launcher)
                self.assertEqual("invalid_launcher", caught.exception.code)
                popen.assert_not_called()
                self.assertFalse((self.attempt / "launch.json").exists())

    def test_codex_process_resume_is_rejected_before_launch(self):
        with patch.object(executor.subprocess, "Popen") as popen:
            with self.assertRaises(executor.WudiTaskError) as caught:
                self.launch(backend="codex", resume=True)
        self.assertEqual("unsupported_resume", caught.exception.code)
        popen.assert_not_called()
        self.assertFalse((self.attempt / "launch.json").exists())

    def test_codex_process_uses_stdin_without_bypass(self):
        self.fake(
            "import json,sys\nassert sys.argv[1:] == ['exec', '--json', '-']\nassert sys.stdin.read()\nprint(json.dumps({'type':'thread.started','thread_id':'thread'}))\nprint(json.dumps({'type':'turn.completed'}))\n",
            "codex",
        )
        self.launch(backend="codex")
        self.assertEqual("candidate", self.wait()["result"]["outcome"])


if __name__ == "__main__":
    unittest.main()
