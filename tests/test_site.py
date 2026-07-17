from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from wuditask.errors import WudiTaskError
from wuditask.site_builder import build_site
from wuditask.workflow import archive_task, claim_task

from tests.helpers import ACTOR, add_task, make_repository

ROOT = Path(__file__).resolve().parents[1]


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
            claim_task(repository, ACTOR, task_id=task_id)
            output = base / "public"

            result = build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
                hub_repo="acme/wuditask",
            )
            snapshot = json.loads((output / "snapshot.json").read_text())

            self.assertEqual(1, result["counts"]["in_progress"])
            self.assertEqual("acme/wuditask", snapshot["hub_repo"])
            self.assertEqual(task_id, snapshot["open_tasks"][0]["id"])
            self.assertEqual(
                "text_only",
                snapshot["open_tasks"][0]["delivery"]["delivery_state"],
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
            self.assertNotIn("WUDITASK_INSTALL_CONTENT", install)
            self.assertFalse((output / "install.template.html").exists())
            self.assertEqual(
                (ROOT / "site" / "install.md").read_text(encoding="utf-8"),
                (output / "install.md").read_text(encoding="utf-8"),
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

    def test_reports_all_missing_site_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = make_repository(base / "hub")
            source = base / "site"
            shutil.copytree(ROOT / "site", source)
            (source / "dag.js").unlink()
            (source / "install.template.html").unlink()

            with self.assertRaises(WudiTaskError) as raised:
                build_site(
                    repository.load_index(),
                    source=source,
                    output=base / "public",
                )

            self.assertEqual("site_source_missing", raised.exception.code)
            self.assertEqual(
                ["dag.js", "install.template.html"],
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
            claim_task(repository, ACTOR, task_id=task_id)
            archive_task(
                repository,
                ACTOR,
                task_id,
                outcome="done",
                result="Verified.",
                evidence={"AC-1": "Regression command passed."},
                now="2026-07-11T13:00:00Z",
            )
            output = base / "public"

            build_site(
                repository.load_index(),
                source=ROOT / "site",
                output=output,
            )
            snapshot = json.loads((output / "snapshot.json").read_text())

            self.assertEqual("done", snapshot["archived_tasks"][0]["derived"]["state"])
            self.assertTrue(snapshot["archived_tasks"][0]["derived"]["ready"])


if __name__ == "__main__":
    unittest.main()
