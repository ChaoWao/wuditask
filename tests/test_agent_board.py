from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from wuditask.agent_board import render_board


class BoardDOM(HTMLParser):
    def __init__(self, source: str):
        super().__init__()
        self.tags = []
        self.ids = []
        self.links = []
        self.scripts = []
        self._script = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs["href"])
        if tag == "script":
            self._script = {"attrs": attrs, "source": ""}
            self.scripts.append(self._script)

    def handle_data(self, data):
        if self._script is not None:
            self._script["source"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None


def fixture():
    return {
        "goal": "Deliver reliable asynchronous execution",
        "revision": 7,
        "updated_at": "2026-10-24T12:00:00Z",
        "agreement": {"scope": "Full lifecycle", "acceptance": ["Cancellation works"]},
        "items": {
            "design": {
                "title": "Agree lifecycle",
                "status": "accepted",
                "depends_on": [],
            },
            "backend": {
                "title": "Implement backend",
                "status": "running",
                "summary": "Adding cancellation",
                "active_attempt": "attempt-1",
                "depends_on": [{"kind": "artifact", "ref": "design"}],
            },
            "tests": {
                "title": "Validate cancellation",
                "status": "blocked",
                "block_reason": "Waiting for fixed test input",
                "depends_on": [
                    {"kind": "artifact", "ref": "design"},
                    {"kind": "decision", "ref": "cost"},
                ],
            },
            "integrate": {
                "title": "Integrate the feature",
                "status": "planned",
                "depends_on": [
                    {"kind": "artifact", "ref": "backend"},
                    {"kind": "artifact", "ref": "tests"},
                    {"kind": "merged", "ref": "pr"},
                ],
            },
        },
        "attempts": {
            "attempt-1": {
                "backend": "codex",
                "status": "running",
                "summary": "Editing backend",
            }
        },
        "decisions": {
            "cost": {
                "question": "Allow an additional worker?",
                "proposal_revision": 3,
                "status": "pending",
            },
            "prior": {
                "question": "Use the agreed API?",
                "proposal_revision": 1,
                "status": "approved",
            },
        },
        "deliveries": {
            "pr": {
                "title": "Complete async lifecycle",
                "status": "ready_for_merge",
                "head": "abc123",
                "pr_url": "https://github.com/example/repo/pull/1",
                "review_evidence": {
                    "head": "abc123",
                    "status": "passed",
                    "observed_at": "2026-10-24T11:00:00Z",
                },
                "ci_evidence": {"head": "abc123", "status": "passed"},
            }
        },
    }


class AgentBoardTests(unittest.TestCase):
    def render(self, plan):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "board.html"
            render_board(plan, output)
            return output.read_text(encoding="utf-8")

    def test_exited_attempt_is_awaiting_review_across_board_views(self):
        for backend, status in (("claude", "awaiting_review"), ("codex-native", "running")):
            with self.subTest(backend=backend):
                plan = fixture()
                plan["attempts"]["attempt-1"].update(
                    backend=backend, status=status, observation={"state": "exited"}
                )
                original = copy.deepcopy(plan)
                source = self.render(plan)
                self.assertIn('id="item-1" data-status="awaiting_review"', source)
                self.assertIn(f"awaiting review · Executor: {backend}", source)
                self.assertIn("artifact: awaiting review", source)
                self.assertIn("Review the result and acceptance evidence.", source)
                self.assertNotIn("Collect the active attempt", source)
                self.assertEqual(original, plan)

    def test_unknown_attempt_requires_reconciliation_and_cancellation_stays_terminal(self):
        plan = fixture()
        plan["attempts"]["attempt-1"].update(
            status="launch_unknown", observation={"state": "unknown"}
        )
        source = self.render(plan)
        self.assertIn('id="item-1" data-status="launch_unknown"', source)
        self.assertIn("launch unknown · Executor: codex", source)
        self.assertIn("Reconcile the recorded attempt before starting another worker.", source)
        plan["items"]["backend"]["status"] = "cancelled"
        source = self.render(plan)
        self.assertIn('id="item-1" data-status="cancelled"', source)
        self.assertIn("cancellation does not satisfy dependencies", source)
        self.assertNotIn("launch unknown · Executor: codex", source)

    def test_merge_gate_explains_baseline_requirement_and_commit_pin(self):
        plan = fixture()
        delivery = plan["deliveries"]["pr"]
        delivery.update(status="merged", merge_evidence={"merge_commit": "actual-merge"})
        gate = plan["items"]["integrate"]["depends_on"][-1]
        source = self.render(plan)
        self.assertIn("Merge gate: pending; requires baseline verification; observed merged", source)
        gate["require_baseline"] = False
        source = self.render(plan)
        self.assertIn("Merge gate: satisfied; requires verified merge; observed merged", source)
        gate.update(require_baseline=True, merge_commit="expected-merge")
        delivery["status"] = "baseline_verified"
        source = self.render(plan)
        self.assertIn("Merge gate: pending; requires baseline verification; observed baseline verified", source)
        self.assertIn("required merge commit expected-merge; observed merge commit actual-merge", source)
        delivery["merge_evidence"]["merge_commit"] = "expected-merge"
        source = self.render(plan)
        self.assertIn("Merge gate: satisfied; requires baseline verification; observed baseline verified", source)

    def test_merge_gate_escapes_required_and_observed_commits(self):
        plan = fixture()
        attack = '<img src=x onerror="alert(1)">'
        plan["items"]["integrate"]["depends_on"][-1]["merge_commit"] = attack
        plan["deliveries"]["pr"].update(
            status="baseline_verified", merge_evidence={"merge_commit": attack}
        )
        source = self.render(plan)
        self.assertNotIn(attack, source)
        self.assertIn("required merge commit &lt;img", source)
        self.assertFalse(any(tag == "img" for tag, _ in BoardDOM(source).tags))

    def test_true_fork_join_edges_and_accessible_links(self):
        plan = fixture()
        original = copy.deepcopy(plan)
        source = self.render(plan)
        dom = BoardDOM(source)
        edges = json.loads(dom.scripts[0]["source"])
        self.assertEqual(
            [
                ["item-0", "item-1"],
                ["item-0", "item-2"],
                ["item-1", "item-3"],
                ["item-2", "item-3"],
            ],
            edges,
        )
        self.assertNotIn(["item-1", "item-2"], edges)
        self.assertEqual(len(dom.ids), len(set(dom.ids)))
        for link in dom.links:
            if link.startswith("#"):
                self.assertIn(link[1:], dom.ids)
        self.assertEqual(
            4,
            sum(
                tag == "details" and attrs.get("class") == "node"
                for tag, attrs in dom.tags
            ),
        )
        self.assertIn("Dependency table (accessible fallback)", source)
        self.assertIn("Waiting for fixed test input", source)
        self.assertIn("Executor: codex", source)
        self.assertIn("Allow an additional worker?", source)
        self.assertNotIn("Use the agreed API?", source)
        self.assertEqual(original, plan)

    def test_snapshot_time_and_readiness_sections_are_distinct(self):
        source = self.render(fixture())
        self.assertIn("Revision 7", source)
        self.assertIn("Plan observed: 2026-10-24T12:00:00Z", source)
        self.assertIn("does not refresh or verify remote state", source)
        self.assertIn('id="decisions-title"', source)
        self.assertIn('id="ready-title"', source)
        self.assertNotIn("Ready withdrawn", source)
        self.assertIn("2026-10-24T11:00:00Z", source)

    def test_old_or_missing_evidence_withdraws_ready(self):
        for field, value in [
            ("review_evidence", {"head": "old", "status": "passed"}),
            ("ci_evidence", None),
            ("head", "new"),
            ("stale", True),
            ("error", "Remote observation failed"),
            ("ci_evidence", {"head": "abc123", "status": "failed"}),
        ]:
            with self.subTest(field=field, value=value):
                plan = fixture()
                plan["deliveries"]["pr"][field] = value
                source = self.render(plan)
                self.assertIn("Ready withdrawn", source)
                self.assertIn("needs revalidation", source)
                self.assertIn("No PR has recorded current-head readiness", source)

    def test_empty_blocked_and_merged_are_not_reported_as_complete(self):
        empty = self.render({})
        self.assertIn("No work items recorded", empty)
        self.assertIn("The plan is not complete", empty)
        plan = fixture()
        plan["deliveries"]["pr"]["status"] = "merged"
        source = self.render(plan)
        self.assertIn('class="status">merged</span>', source)
        self.assertNotIn('class="status">baseline verified</span>', source)
        self.assertIn("No PR has recorded current-head readiness", source)
        self.assertIn("Resolve the recorded blocker", source)

    def test_cycle_and_unknown_dependency_remain_visible(self):
        plan = fixture()
        plan["items"]["design"]["depends_on"] = [
            {"kind": "artifact", "ref": "integrate"}
        ]
        plan["items"]["tests"]["depends_on"].append(
            {"kind": "artifact", "ref": "missing"}
        )
        source = self.render(plan)
        self.assertIn("Dependency cycle detected", source)
        self.assertIn("unresolved dependency missing", source)
        dom = BoardDOM(source)
        self.assertTrue(all(f"item-{index}" in dom.ids for index in range(4)))

    def test_untrusted_values_cannot_create_markup_scripts_or_urls(self):
        attack = '</script><img src=x onerror="alert(1)"><script>alert(2)</script>'
        plan = fixture()
        plan["goal"] = attack
        plan["items"]["backend"]["summary"] = attack
        plan["items"]["backend"]["write_scope"] = [attack]
        plan["decisions"]["cost"]["question"] = attack
        plan["deliveries"]["pr"]["title"] = attack
        plan["deliveries"]["pr"]["review_evidence"]["reference"] = attack
        for url in (
            "javascript:alert(1)",
            "data:text/html,attack",
            "file:///secret",
            "//example.com",
            "https:\n//example.com",
        ):
            with self.subTest(url=url):
                plan["deliveries"]["pr"]["pr_url"] = url
                source = self.render(plan)
                dom = BoardDOM(source)
                self.assertEqual(2, len(dom.scripts))
                self.assertFalse(
                    any(tag in {"img", "iframe", "object"} for tag, _ in dom.tags)
                )
                self.assertTrue(all(link.startswith("#") for link in dom.links))
                self.assertIn("&lt;/script&gt;", source)
                self.assertFalse(any("src" in attrs for _, attrs in dom.tags))
                self.assertNotIn(attack, source)
        plan["deliveries"]["pr"]["pr_url"] = 'https://example.com/?q="x"&a=1'
        dom = BoardDOM(self.render(plan))
        self.assertIn('https://example.com/?q="x"&a=1', dom.links)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not available")
    def test_script_syntax_and_expand_collapse_dom_behavior(self):
        dom = BoardDOM(self.render(fixture()))
        script = dom.scripts[1]["source"]
        checked = subprocess.run(
            [shutil.which("node"), "--check"],
            input=script,
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, checked.returncode, checked.stderr)
        harness = r"""
const assert = require('assert');
const nodes = Array.from({length:4}, (_, i) => ({
  open:false, status:i === 0 ? 'accepted' : 'running', listeners:{},
  addEventListener(name, handler) { this.listeners[name] = handler; },
  getBoundingClientRect() { return {left:i*300, right:i*300+280, top:0, height:100}; }
}));
const buttons = {};
for (const name of ['expand','collapse','collapse-accepted']) buttons[name] = {
  addEventListener(event, handler) { this.click = handler; }
};
const paths = [];
const elements = {
  ...buttons,
  'graph': {scrollWidth:1200, scrollHeight:100, getBoundingClientRect() { return {left:0, top:0}; }},
  'graph-edges': {textContent:'[["item-0","item-1"],["item-0","item-2"],["item-1","item-3"],["item-2","item-3"]]'},
  'edges': {replaceChildren() {paths.length=0;},setAttribute(){},appendChild(p){paths.push(p);}}
};
nodes.forEach((node,i) => elements['item-'+i] = node);
global.document = {
  getElementById(id){assert.ok(elements[id],id);return elements[id];},
  querySelectorAll(selector){return selector.includes('accepted') ? nodes.filter(n=>n.status==='accepted') : nodes;},
  createElementNS(){return {setAttribute(k,v){this[k]=v;}};}
};
global.window = {addEventListener(){}};
"""
        assertions = r"""
assert.strictEqual(paths.length,4);
buttons.expand.click();assert.ok(nodes.every(n=>n.open));
buttons['collapse-accepted'].click();assert.strictEqual(nodes[0].open,false);
assert.ok(nodes.slice(1).every(n=>n.open));
buttons.collapse.click();assert.ok(nodes.every(n=>!n.open));
nodes[1].listeners.toggle();assert.strictEqual(paths.length,4);
assert.ok(paths.every(path=>path.d.startsWith('M ')));
"""
        ran = subprocess.run(
            [shutil.which("node")],
            input=harness + script + assertions,
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
