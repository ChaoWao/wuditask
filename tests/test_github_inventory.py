import json
from pathlib import Path
import tempfile
import unittest

from tests.test_github_graph import MemoryReader, issue, snapshot, node
from wuditask.github_graph import GraphScope, Reference, explicit_issue_links


def pr(n, **fields):
    return issue(
        n,
        html_url=f"https://github.com/acme/service/pull/{n}",
        node_id=f"PR_{n}",
        head={"sha": "abc"},
        **fields,
    )


class InventoryTests(unittest.TestCase):
    def test_pr_author_is_separate_from_assignee_and_committer(self):
        for user in ({"login": "writer"}, None):
            with self.subTest(user=user):
                reader = MemoryReader()
                value = pr(
                    3,
                    user=user,
                    assignees=[{"login": "reviewer"}],
                    committer={"login": "integrator"},
                )
                reader.add(value)
                reader.relations["repos/acme/service/issues?state=open"] = [value]
                result = node(snapshot(reader, repositories=("acme/service",)), 3)
                self.assertEqual(result["author"], "writer" if user else None)
                self.assertEqual(result["owner_resolution"], "not_applicable")
                self.assertNotIn("committer", result)

    def test_inventory_outside_root_and_reverse_link(self):
        reader = MemoryReader()
        reader.add(issue(2, state="closed"))
        reader.add(pr(3))
        reader.add(pr(4))
        reader.relations["repos/acme/service/issues?state=open"] = [pr(3), pr(4)]
        reader.prs[pr(3)["html_url"]] = [issue(2)["html_url"]]
        data = snapshot(reader, repositories=("acme/service",))
        self.assertEqual(data["status"], "complete")
        self.assertEqual({n["number"] for n in data["nodes"]}, {1, 2, 3, 4})
        self.assertEqual(len(data["edges"]), 1)
        self.assertEqual(data["edges"][0]["from"], node(data, 3)["id"])
        self.assertEqual(data["edges"][0]["to"], node(data, 2)["id"])
        self.assertEqual(data["roots"], [node(data, 1)["id"]])

    def test_explicit_body_association_never_blocks(self):
        reader = MemoryReader()
        reader.add(issue(2))
        reader.add(pr(3, body="Part of #2."))
        reader.relations["repos/acme/service/issues?state=open"] = [pr(3)]
        data = snapshot(reader, repositories=("acme/service",))
        self.assertEqual(data["edges"][0]["kind"], "relates")
        self.assertEqual(data["edges"][0]["source"], pr(3)["html_url"])
        self.assertEqual(node(data, 2)["prerequisites"], "unrecorded")
        reader.prs[pr(3)["html_url"]] = [issue(2)["html_url"]]
        self.assertEqual(
            [
                e["kind"]
                for e in snapshot(reader, repositories=("acme/service",))["edges"]
            ],
            ["delivers"],
        )

    def test_mentions_code_and_hidden_text_do_not_associate(self):
        body = """Related #2
<!-- Part of #3. -->
````
Part of #4.
```
Part of #5.
````
<details>
Part of #6.
</details>
- Part of #7.
Part of https://github.com/acme/other/issues/8.
"""
        self.assertEqual(
            explicit_issue_links(body, "acme/service"),
            [
                Reference("acme/service", "issue", 7),
                Reference("acme/other", "issue", 8),
            ],
        )

    def test_inventory_failure_and_private_repo_are_not_empty_success(self):
        reader = MemoryReader()
        reader.failures.add("repos/acme/service/issues?state=open")
        self.assertEqual(
            snapshot(reader, repositories=("acme/service",))["status"], "partial"
        )
        reader.private.add("secret/private")
        data = snapshot(reader, repositories=("secret/private",))
        self.assertEqual(data["status"], "partial")
        self.assertNotIn("secret/private", json.dumps(data))
        self.assertNotIn("repos/secret/private/issues?state=open", reader.calls)

    def test_scope_accepts_repository_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "scope.json"
            p.write_text(
                json.dumps(
                    {"roots": [issue(1)["html_url"]], "repositories": ["Acme/Service"]}
                )
            )
            self.assertEqual(GraphScope.read(p).repositories, ("acme/service",))
