from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wuditask.business_site import build_business_site
from wuditask.cli import _parser, run
from wuditask.errors import WudiTaskError
from wuditask.github_graph import (
    GraphScope,
    GitHubReader,
    ReadError,
    Reference,
    _checks,
    collect_graph,
    dependency_cycles,
)

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-22T00:00:00Z"


def issue(n, repo="acme/service", **fields):
    return {
        "node_id": f"I_{repo}_{n}",
        "html_url": f"https://github.com/{repo}/issues/{n}",
        "title": f"Issue {n}",
        "number": n,
        "state": "open",
        "state_reason": None,
        "assignees": [{"login": "alice"}],
        "updated_at": STAMP,
        "body": "BODY MUST NOT BE PUBLISHED",
        **fields,
    }


class MemoryReader:
    def __init__(self):
        self.objects = {"repos/acme/service/issues/1": issue(1)}
        self.relations = {}
        self.prs = {}
        self.private = set()
        self.failures = set()
        self.changed = set()
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        if endpoint in self.failures:
            raise ReadError("github_unavailable")
        if len(endpoint.split("/")) == 3:
            return {"private": endpoint.removeprefix("repos/") in self.private}
        result = deepcopy(self.objects[endpoint])
        if endpoint in self.changed and self.calls.count(endpoint) > 1:
            result["updated_at"] = "2026-10-22T01:00:00Z"
        return result

    def pages(self, endpoint, **kwargs):
        self.calls.append(endpoint)
        if endpoint in self.failures:
            raise ReadError("page_limit")
        return deepcopy(self.relations.get(endpoint, []))

    def closing_prs(self, ref):
        return self.prs.get(ref.url, [])

    def review_decision(self, ref):
        return "APPROVED"

    def add(self, obj):
        ref = Reference.parse(obj["html_url"])
        self.objects[ref.endpoint] = obj
        return ref

    def link(self, source, target, kind):
        self.add(source)
        self.add(target)
        ref = Reference.parse(source["html_url"])
        suffix = "/sub_issues" if kind == "contains" else "/dependencies/blocked_by"
        self.relations.setdefault(ref.endpoint + suffix, []).append(target)


def snapshot(reader, **options):
    return collect_graph(
        GraphScope((Reference("acme/service", "issue", 1),), **options), reader
    )


def node(data, n, repo="acme/service"):
    return next(
        v for v in data["nodes"] if v.get("repo") == repo and v.get("number") == n
    )


class GraphTests(unittest.TestCase):
    def test_native_relations_and_no_hub_registration(self):
        reader = MemoryReader()
        reader.link(issue(1), issue(2), "contains")
        reader.link(
            issue(2),
            issue(8, "acme/lib", state="closed", state_reason="completed"),
            "blocks",
        )
        data = snapshot(reader)
        self.assertEqual("complete", data["status"])
        self.assertEqual("satisfied", node(data, 2)["prerequisites"])
        self.assertEqual("unrecorded", node(data, 1)["prerequisites"])
        self.assertEqual({"contains", "blocks"}, {e["kind"] for e in data["edges"]})
        edge = next(e for e in data["edges"] if e["kind"] == "blocks")
        self.assertEqual(node(data, 8, "acme/lib")["id"], edge["from"])
        self.assertNotIn("BODY MUST", json.dumps(data))
        self.assertNotIn("priority", json.dumps(data))
        self.assertNotIn("completion", node(data, 2))

    def test_cancelled_and_reopened_prerequisites(self):
        reader = MemoryReader()
        reader.link(
            issue(1), issue(2, state="closed", state_reason="not_planned"), "blocks"
        )
        self.assertEqual("needs_assessment", node(snapshot(reader), 1)["prerequisites"])
        reader.objects["repos/acme/service/issues/2"].update(
            state="open", state_reason=None
        )
        self.assertEqual("blocked", node(snapshot(reader), 1)["prerequisites"])
        reader.objects["repos/acme/service/issues/2"].update(
            state="closed", state_reason="completed"
        )
        self.assertEqual("satisfied", node(snapshot(reader), 1)["prerequisites"])

    def test_private_dependency_is_anonymous(self):
        reader = MemoryReader()
        reader.private.add("secret/private")
        reader.link(
            issue(1),
            issue(
                27,
                "secret/private",
                title="SECRET TITLE",
                state="closed",
                state_reason="completed",
            ),
            "blocks",
        )
        data = snapshot(reader)
        output = json.dumps(data)
        self.assertNotIn("SECRET", output)
        self.assertNotIn("secret/private", output)
        self.assertEqual("unknown", node(data, 1)["prerequisites"])
        self.assertEqual("partial", data["status"])
        self.assertNotIn("repos/secret/private/issues/27", reader.calls)

    def test_relation_failure_and_observation_change_do_not_report_satisfied(self):
        reader = MemoryReader()
        reader.failures.add("repos/acme/service/issues/1/dependencies/blocked_by")
        self.assertEqual("unknown", node(snapshot(reader), 1)["prerequisites"])
        reader.failures.clear()
        reader.calls.clear()
        reader.changed.add("repos/acme/service/issues/1")
        self.assertEqual("unknown", node(snapshot(reader), 1)["prerequisites"])

    def test_limits_are_visible_and_do_not_create_fake_satisfied_dependency(self):
        reader = MemoryReader()
        reader.link(
            issue(1), issue(2, state="closed", state_reason="completed"), "blocks"
        )
        data = snapshot(reader, max_nodes=1)
        self.assertEqual(1, len(data["nodes"]))
        self.assertEqual("unknown", node(data, 1)["prerequisites"])
        self.assertIn("node_limit", [d["code"] for d in data["diagnostics"]])

    def test_shared_dependency_is_read_once_and_cycle_downstream_is_invalid(self):
        reader = MemoryReader()
        reader.link(issue(1), issue(2), "contains")
        reader.link(issue(1), issue(3), "contains")
        reader.link(issue(2), issue(4), "blocks")
        reader.link(issue(3), issue(4), "blocks")
        reader.link(issue(4), issue(2), "blocks")
        data = snapshot(reader)
        self.assertEqual(
            2, reader.calls.count("repos/acme/service/issues/4")
        )  # Read + consistency check.
        self.assertEqual("invalid", node(data, 3)["prerequisites"])
        self.assertEqual("unrecorded", node(data, 1)["prerequisites"])

    def test_scc_includes_overlapping_cycles_and_handles_deep_graphs(self):
        nodes = {str(n): {} for n in range(2000)}
        edges = [
            {"from": str(n), "to": str(n + 1), "kind": "blocks"} for n in range(1999)
        ]
        edges.extend(
            [
                {"from": "1999", "to": "1997", "kind": "blocks"},
                {"from": "1998", "to": "1996", "kind": "blocks"},
            ]
        )
        self.assertEqual(
            {"1996", "1997", "1998", "1999"}, dependency_cycles(nodes, edges)
        )

    def test_pr_merge_does_not_complete_issue_and_head_is_recorded(self):
        reader = MemoryReader()
        pr = issue(
            5,
            html_url="https://github.com/acme/service/pull/5",
            node_id="PR_5",
            state="closed",
            head={"sha": "a" * 40},
            merged_at=STAMP,
            draft=False,
        )
        reader.add(pr)
        reader.prs[issue(1)["html_url"]] = [pr["html_url"]]
        data = snapshot(reader)
        self.assertEqual("open", node(data, 1)["state"])
        self.assertEqual("merged", node(data, 5)["state"])
        self.assertEqual("a" * 40, node(data, 5)["head_sha"])
        self.assertEqual("delivers", data["edges"][0]["kind"])
        reader.calls.clear()
        reader.changed.add("repos/acme/service/pulls/5")
        changed = snapshot(reader)
        self.assertEqual("partial", changed["status"])
        self.assertIsNone(node(changed, 5)["checks"])

    def test_owner_is_not_inferred_from_pr_author(self):
        reader = MemoryReader()
        reader.objects["repos/acme/service/issues/1"]["assignees"] = []
        self.assertEqual("unassigned", node(snapshot(reader), 1)["owner_resolution"])
        reader.objects["repos/acme/service/issues/1"]["assignees"] = [
            {"login": "alice"},
            {"login": "bob"},
        ]
        self.assertEqual("conflict", node(snapshot(reader), 1)["owner_resolution"])

    def test_aliases_normalize_by_node_identity(self):
        reader = MemoryReader()
        same = issue(9)
        reader.objects["repos/acme/service/issues/1"] = same
        reader.add(same)
        data = collect_graph(
            GraphScope(
                (
                    Reference("acme/service", "issue", 1),
                    Reference("acme/service", "issue", 9),
                )
            ),
            reader,
        )
        self.assertEqual(1, len(data["nodes"]))
        self.assertEqual(1, len(set(data["roots"])))


class ReaderTests(unittest.TestCase):
    def test_rest_pagination_and_limit(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps([{}] * (100 if "&page=1" in command[2] else 1)),
                "",
            )

        self.assertEqual(
            101, len(GitHubReader(runner=run).pages("repos/a/b/issues/1/sub_issues"))
        )
        self.assertIn("per_page=100&page=2", calls[1][2])
        with self.assertRaisesRegex(ReadError, "page_limit"):
            GitHubReader(runner=run, max_pages=1).pages("test")

    def test_graphql_pagination_and_partial_errors(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            more = len(calls) == 1
            connection = {
                "nodes": [{"url": f"https://github.com/a/b/pull/{len(calls)}"}],
                "pageInfo": {"hasNextPage": more, "endCursor": "next"},
            }
            value = {
                "data": {
                    "repository": {
                        "issue": {"closedByPullRequestsReferences": connection}
                    }
                }
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

        self.assertEqual(
            2, len(GitHubReader(runner=run).closing_prs(Reference("a/b", "issue", 1)))
        )
        self.assertIn("cursor=next", calls[1])

        def partial(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"data": {}, "errors": [{"message": "PRIVATE URL"}]}),
                "",
            )

        with self.assertRaisesRegex(ReadError, "graphql_incomplete"):
            GitHubReader(runner=partial).get("test")

    def test_timeout_and_stderr_are_sanitized(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "TOKEN PRIVATE URL")

        with self.assertRaisesRegex(ReadError, "^github_unavailable$"):
            GitHubReader(runner=failed).get("test")

        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 45)

        with self.assertRaisesRegex(ReadError, "^transport_error$"):
            GitHubReader(runner=timeout).get("test")

    def test_latest_status_overrides_older_failure(self):
        status = [
            {"id": 2, "context": "ci", "state": "success"},
            {"id": 1, "context": "ci", "state": "failure"},
        ]
        checks = [
            {"status": "completed", "conclusion": "skipped"},
            {"status": "in_progress", "conclusion": None},
        ]
        self.assertEqual(
            dict(total=3, successful=1, skipped=1, pending=1, failed=0),
            _checks(checks, status),
        )


class PublicationTests(unittest.TestCase):
    def test_cli_build_requires_no_installation_and_keeps_hub_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope.json"
            scope.write_text(json.dumps({"roots": [issue(1)["html_url"]]}))
            args = _parser().parse_args(
                [
                    "build-site",
                    "--github-scope",
                    str(scope),
                    "--output",
                    str(Path(tmp) / "site"),
                ]
            )
            with (
                patch(
                    "wuditask.cli._coordinator",
                    side_effect=AssertionError("Hub must not be read"),
                ),
                patch(
                    "wuditask.business_site.collect_graph",
                    return_value=snapshot(MemoryReader()),
                ),
            ):
                result = run(args, ROOT)
            self.assertEqual("complete", result["status"])
            out = Path(result["output"])
            self.assertTrue((out / "index.html").exists())
            self.assertFalse((out / "snapshot.json").exists())

    def test_failed_root_preserves_prior_artifact_and_partial_generation_is_explicit(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            reader = MemoryReader()
            scope = GraphScope((Reference("acme/service", "issue", 1),))
            build_business_site(scope, source=ROOT / "site", output=out, reader=reader)
            before = (out / "graph.json").read_bytes()
            reader.failures.add("repos/acme/service/issues/1")
            with self.assertRaises(WudiTaskError):
                build_business_site(
                    scope, source=ROOT / "site", output=out, reader=reader
                )
            self.assertEqual(before, (out / "graph.json").read_bytes())
            reader.failures.clear()
            reader.failures.add("repos/acme/service/issues/1/sub_issues")
            result = build_business_site(
                scope, source=ROOT / "site", output=out, reader=reader
            )
            self.assertEqual("partial", result["status"])
            self.assertEqual(
                "partial", json.loads((out / "graph.json").read_text())["status"]
            )

    def test_unrelated_directory_and_symlink_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            out.mkdir()
            (out / "user.txt").write_text("keep")
            scope = GraphScope((Reference("acme/service", "issue", 1),))
            for candidate in (out, Path(tmp) / "link"):
                if candidate.name == "link":
                    candidate.symlink_to(out, target_is_directory=True)
                with self.assertRaises(WudiTaskError):
                    build_business_site(
                        scope,
                        source=ROOT / "site",
                        output=candidate,
                        reader=MemoryReader(),
                    )
            self.assertEqual("keep", (out / "user.txt").read_text())

    def test_scope_rejects_unknown_fields_and_bad_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scope.json"
            for extra in (
                {"token": "not-a-real-token"},
                {"max_nodes": True},
                {"max_pages": 0},
            ):
                path.write_text(json.dumps({"roots": [issue(1)["html_url"]], **extra}))
                with self.assertRaises(WudiTaskError):
                    GraphScope.read(path)

    def test_browser_model(self):
        subprocess.run(
            ["node", str(ROOT / "tests/github_graph_harness.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
