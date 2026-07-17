from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from wuditask.errors import WudiTaskError
from wuditask.site_builder import build_site
from wuditask.workflow import archive_task, release_agent, start_agent

from tests.helpers import (
    ACTOR,
    OTHER_ACTOR,
    OTHER_RUN_ID,
    RUN_ID,
    add_task,
    make_repository,
)

ROOT = Path(__file__).resolve().parents[1]


def _fresh_delivery(
    source: dict[str, object],
    *,
    state: str = "review",
) -> dict[str, object]:
    repo = str(source["repo"])
    number = int(source["number"])
    noun = "pull" if source["kind"] == "github_pull_request" else "issues"
    return {
        "status": "fresh",
        "delivery_state": state,
        "title": f"Canonical delivery #{number}",
        "body": "Acceptance and implementation details live on GitHub.",
        "owners": ["alice", "bob"],
        "assignees": ["alice", "bob"],
        "prs": [
            {
                "repo": repo,
                "number": number + 1,
                "url": f"https://github.com/{repo}/pull/{number + 1}",
                "title": "Linked implementation",
                "body": "Implementation details.",
                "author": "bob",
                "assignees": ["alice"],
                "state": "OPEN",
                "is_draft": False,
                "merged_at": None,
                "review_decision": "REVIEW_REQUIRED",
                "merge_state_status": "BLOCKED",
                "checks": {"total": 3, "successful": 2, "pending": 1, "failed": 0},
                "updated_at": "2026-07-11T12:30:00Z",
            }
        ],
        "updated_at": "2026-07-11T12:30:00Z",
        "fetched_at": "2026-07-11T12:31:00Z",
        "error": None,
        "url": f"https://github.com/{repo}/{noun}/{number}",
    }


class _NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | None]] = []
        self.main_ids: list[str | None] = []
        self._nav_label: str | None = None
        self._link: dict[str, str | None] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "nav":
            self._nav_label = attributes.get("aria-label")
        elif tag == "a" and self._nav_label is not None:
            self._link = {
                "group": self._nav_label,
                "href": attributes.get("href"),
                "current": attributes.get("aria-current"),
                "text": "",
            }
        elif tag == "main":
            self.main_ids.append(attributes.get("id"))

    def handle_data(self, data: str) -> None:
        if self._link is not None:
            self._link["text"] = str(self._link["text"] or "") + data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link is not None:
            self._link["text"] = str(self._link["text"] or "").strip()
            self.links.append(self._link)
            self._link = None
        elif tag == "nav":
            self._nav_label = None


class SiteTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is not available")
    def test_filter_options_follow_the_active_view(self) -> None:
        process = subprocess.run(
            [
                shutil.which("node") or "node",
                str(ROOT / "tests" / "site_filter_harness.js"),
                str(ROOT / "site" / "app.js"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, process.returncode, process.stdout + process.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not available")
    def test_dependency_graph_supports_global_and_repository_views(self) -> None:
        process = subprocess.run(
            [
                shutil.which("node") or "node",
                str(ROOT / "tests" / "site_dag_harness.js"),
                str(ROOT / "site" / "dag.js"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, process.returncode, process.stdout + process.stderr)

    def test_builds_static_snapshot_without_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            task_id = "WDT-20260711T120000Z-A1B2C3"
            add_task(repository, task_id)
            with patch(
                "wuditask.workflow.fetch_delivery",
                side_effect=lambda source: _fresh_delivery(source),
            ):
                start_agent(repository, ACTOR, task_id=task_id, run_id=RUN_ID)
                release_agent(repository, ACTOR, task_id, run_id=RUN_ID)
                start_agent(repository, ACTOR, task_id=task_id, run_id=RUN_ID)
                start_agent(
                    repository,
                    OTHER_ACTOR,
                    task_id=task_id,
                    run_id=OTHER_RUN_ID,
                )
            output = base / "public"

            result = build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
                hub_repo="acme/wuditask",
                delivery_fetcher=_fresh_delivery,
            )
            snapshot = json.loads((output / "snapshot.json").read_text())

            self.assertEqual(3, snapshot["schema_version"])
            self.assertEqual(1, result["counts"]["in_progress"])
            self.assertEqual("acme/wuditask", snapshot["hub_repo"])
            self.assertEqual(task_id, snapshot["open_tasks"][0]["id"])
            self.assertEqual(
                [{"login": "alice"}, {"login": "bob"}],
                snapshot["open_tasks"][0]["active_agents"],
            )
            self.assertEqual(
                [{"login": "alice"}, {"login": "bob"}],
                snapshot["open_tasks"][0]["derived"]["active_agents"],
            )
            self.assertEqual(
                "review",
                snapshot["open_tasks"][0]["delivery"]["delivery_state"],
            )
            self.assertEqual(
                "Canonical delivery #12",
                snapshot["open_tasks"][0]["delivery"]["title"],
            )
            self.assertEqual(
                "Acceptance and implementation details live on GitHub.",
                snapshot["open_tasks"][0]["delivery"]["body"],
            )
            self.assertEqual(
                ["alice", "bob"],
                snapshot["open_tasks"][0]["delivery"]["owners"],
            )
            self.assertEqual(
                {"total": 3, "successful": 2, "pending": 1, "failed": 0},
                snapshot["open_tasks"][0]["delivery"]["prs"][0]["checks"],
            )
            published = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn('"run_id"', published)
            self.assertNotIn("WDX-", published)
            self.assertEqual(
                [
                    {"login": "alice", "run_id": RUN_ID},
                    {"login": "bob", "run_id": OTHER_RUN_ID},
                ],
                repository.load_index().open[task_id].task["active_agents"],
            )
            self.assertNotIn("owner", snapshot["open_tasks"][0])
            expected_files = {
                "index.html",
                "styles.css",
                "app.js",
                "dag.html",
                "dag.js",
                "install.md",
                "install.html",
                "workflow.md",
                "workflow.html",
                "snapshot.json",
                ".nojekyll",
                ".wuditask-site",
            }
            self.assertEqual(expected_files, set(result["files"]))
            self.assertEqual(expected_files, {path.name for path in output.iterdir()})

            install = (output / "install.html").read_text(encoding="utf-8")
            self.assertIn('<html lang="zh-CN">', install)
            self.assertIn("<h1>安装与使用 WudiTask</h1>", install)
            self.assertIn("<h2>在新电脑上安装</h2>", install)
            self.assertIn('<pre><code class="language-bash">', install)
            self.assertIn(
                '<a href="https://github.com/ChaoWao/wuditask">WudiTask 工具仓</a>',
                install,
            )
            self.assertIn(
                '<a class="is-active" href="install.html" aria-current="page">',
                install,
            )
            self.assertIn(">Join us</a>", install)
            self.assertIn("<code>$wuditask-check</code>", install)
            self.assertIn("只停止 matching <code>run_id</code>", install)
            self.assertNotIn("wuditask-dep-check", install)
            self.assertNotIn("wuditask-reconcile", install)
            self.assertNotIn("WUDITASK_INSTALL_CONTENT", install)
            self.assertFalse((output / "install.template.html").exists())
            self.assertEqual(
                (ROOT / "site" / "install.md").read_text(encoding="utf-8"),
                (output / "install.md").read_text(encoding="utf-8"),
            )

            workflow = (output / "workflow.html").read_text(encoding="utf-8")
            self.assertIn('<html lang="en">', workflow)
            self.assertIn("<h1>WudiTask workflow</h1>", workflow)
            self.assertIn("<h2>1. Assign on GitHub</h2>", workflow)
            self.assertIn("<h2>2. Execute atomically</h2>", workflow)
            self.assertIn("<h2>3. Check the work</h2>", workflow)
            self.assertIn("<code>active_agents</code>", workflow)
            self.assertIn(
                '<pre><code class="language-bash">wuditask check [TASK_ID]</code></pre>',
                workflow,
            )
            self.assertIn(
                "Release stops only the matching login and <code>run_id</code>",
                workflow,
            )
            self.assertNotIn("dep-check", workflow)
            self.assertNotIn("reconcile", workflow)
            self.assertNotIn("WUDITASK_WORKFLOW_CONTENT", workflow)
            self.assertFalse((output / "workflow.template.html").exists())
            self.assertEqual(
                (ROOT / "site" / "workflow.md").read_text(encoding="utf-8"),
                (output / "workflow.md").read_text(encoding="utf-8"),
            )

    def test_navigation_is_consistent_and_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            output = base / "public"
            build_site(repository.load_index(), source=ROOT / "site", output=output)

            expected_links = [
                ("Learn about WudiTask", "workflow.html", "Workflow"),
                ("Learn about WudiTask", "install.html", "Join us"),
                ("Explore work", "index.html", "Tasks"),
                ("Explore work", "dag.html", "Dependency graph"),
            ]
            pages = {
                "index.html": "index.html",
                "dag.html": "dag.html",
                "install.html": "install.html",
                "workflow.html": "workflow.html",
            }
            for page, current_href in pages.items():
                with self.subTest(page=page):
                    document = (output / page).read_text(encoding="utf-8")
                    self.assertIn(
                        '<a class="skip-link" href="#main-content">Skip to content</a>',
                        document,
                    )
                    parser = _NavigationParser()
                    parser.feed(document)
                    self.assertEqual(["main-content"], parser.main_ids)
                    self.assertEqual(
                        expected_links,
                        [
                            (link["group"], link["href"], link["text"])
                            for link in parser.links
                        ],
                    )
                    self.assertEqual(
                        [current_href],
                        [
                            link["href"]
                            for link in parser.links
                            if link["current"] == "page"
                        ],
                    )

    def test_markdown_renderer_escapes_html_and_rejects_unsafe_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            source = base / "site"
            shutil.copytree(ROOT / "site", source)
            (source / "install.md").write_text(
                """# 安装 <script>alert(1)</script>

段落 <img src=x onerror=alert(1)> 和 `code <b>tag</b>`。

- 无序 <svg onload=alert(1)>

1. 有序一
2. 有序二

[安全链接](https://example.com/?a=1&b=\"two\")
[相对链接](dag.html)
[危险链接](javascript:alert(1))
""",
                encoding="utf-8",
            )
            output = base / "public"

            build_site(repository.load_index(), source=source, output=output)
            install = (output / "install.html").read_text(encoding="utf-8")

            self.assertNotIn("<script>", install)
            self.assertNotIn("<img", install)
            self.assertNotIn("<svg", install)
            self.assertNotIn('href="javascript:', install)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", install)
            self.assertIn("<code>code &lt;b&gt;tag&lt;/b&gt;</code>", install)
            self.assertIn("<ul>", install)
            self.assertIn("<ol>", install)
            self.assertIn(
                'href="https://example.com/?a=1&amp;b=&quot;two&quot;"',
                install,
            )
            self.assertIn('<a href="dag.html">相对链接</a>', install)

    def test_install_template_requires_exactly_one_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            source = base / "site"
            shutil.copytree(ROOT / "site", source)
            template = source / "install.template.html"
            template.write_text(
                template.read_text(encoding="utf-8").replace(
                    "<!-- WUDITASK_INSTALL_CONTENT -->",
                    "",
                ),
                encoding="utf-8",
            )

            with self.assertRaises(WudiTaskError) as raised:
                build_site(
                    repository.load_index(),
                    source=source,
                    output=base / "public",
                )

            self.assertEqual("site_install_template_invalid", raised.exception.code)
            self.assertEqual(0, raised.exception.details["marker_count"])

    def test_workflow_template_requires_exactly_one_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            source = base / "site"
            shutil.copytree(ROOT / "site", source)
            template = source / "workflow.template.html"
            template.write_text(
                template.read_text(encoding="utf-8").replace(
                    "<!-- WUDITASK_WORKFLOW_CONTENT -->",
                    "",
                ),
                encoding="utf-8",
            )

            with self.assertRaises(WudiTaskError) as raised:
                build_site(
                    repository.load_index(),
                    source=source,
                    output=base / "public",
                )

            self.assertEqual("site_workflow_template_invalid", raised.exception.code)
            self.assertEqual(0, raised.exception.details["marker_count"])

    def test_rebuild_accepts_all_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            output = base / "public"

            build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
            )
            (output / "install.html").write_text("stale", encoding="utf-8")
            (output / "install.md").write_text("stale", encoding="utf-8")
            (output / "workflow.html").write_text("stale", encoding="utf-8")
            (output / "workflow.md").write_text("stale", encoding="utf-8")
            (output / "dag.js").write_text("stale", encoding="utf-8")

            build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
            )

            self.assertNotEqual("stale", (output / "install.html").read_text())
            self.assertEqual(
                (ROOT / "site" / "install.md").read_text(encoding="utf-8"),
                (output / "install.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (ROOT / "site" / "dag.js").read_text(encoding="utf-8"),
                (output / "dag.js").read_text(encoding="utf-8"),
            )
            self.assertNotEqual("stale", (output / "workflow.html").read_text())
            self.assertEqual(
                (ROOT / "site" / "workflow.md").read_text(encoding="utf-8"),
                (output / "workflow.md").read_text(encoding="utf-8"),
            )

    def test_reports_all_missing_site_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            source = base / "site"
            shutil.copytree(ROOT / "site", source)
            (source / "dag.js").unlink()
            (source / "install.template.html").unlink()
            (source / "workflow.template.html").unlink()

            with self.assertRaises(WudiTaskError) as raised:
                build_site(
                    repository.load_index(),
                    source=source,
                    output=base / "public",
                )

            self.assertEqual("site_source_missing", raised.exception.code)
            self.assertEqual(
                ["dag.js", "install.template.html", "workflow.template.html"],
                raised.exception.details["missing"],
            )

    def test_refuses_to_clear_unrelated_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            output = base / "output"
            output.mkdir()
            sentinel = output / "keep-me.txt"
            sentinel.write_text("important")

            with self.assertRaisesRegex(Exception, "non-WudiTask"):
                build_site(
                    repository.load_index(),
                    source=ROOT / "site",
                    output=output,
                )
            self.assertEqual("important", sentinel.read_text())

    def test_archived_snapshot_uses_completion_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            task_id = "WDT-20260711T120000Z-A1B2C3"
            add_task(repository, task_id)
            with patch(
                "wuditask.workflow.fetch_delivery",
                side_effect=lambda source: _fresh_delivery(source),
            ):
                start_agent(repository, ACTOR, task_id=task_id, run_id=RUN_ID)
                start_agent(
                    repository,
                    OTHER_ACTOR,
                    task_id=task_id,
                    run_id=OTHER_RUN_ID,
                )
            with patch(
                "wuditask.workflow.fetch_delivery",
                side_effect=lambda source: _fresh_delivery(
                    source,
                    state="verification_needed",
                ),
            ):
                archive_task(
                    repository,
                    ACTOR,
                    task_id,
                    outcome="done",
                    result="Verified.",
                    evidence=["Regression command passed."],
                    run_id=RUN_ID,
                    now="2026-07-11T13:00:00Z",
                )
            output = base / "public"

            build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
                delivery_fetcher=lambda source: _fresh_delivery(
                    source,
                    state="verification_needed",
                ),
            )
            snapshot = json.loads((output / "snapshot.json").read_text())

            self.assertEqual(3, snapshot["schema_version"])
            self.assertEqual("done", snapshot["archived_tasks"][0]["derived"]["state"])
            self.assertTrue(snapshot["archived_tasks"][0]["derived"]["ready"])
            self.assertEqual([], snapshot["archived_tasks"][0]["active_agents"])
            self.assertEqual(
                [{"login": "alice"}, {"login": "bob"}],
                snapshot["archived_tasks"][0]["completion"]["participants"],
            )
            published = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn('"run_id"', published)
            self.assertNotIn("WDX-", published)
            self.assertEqual(
                [
                    {"login": "alice", "run_id": RUN_ID},
                    {"login": "bob", "run_id": OTHER_RUN_ID},
                ],
                repository.load_index()
                .archived[task_id]
                .task["completion"]["participants"],
            )


if __name__ == "__main__":
    unittest.main()
