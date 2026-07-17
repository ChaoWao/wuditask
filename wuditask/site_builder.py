from __future__ import annotations

import html
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .dependencies import dependency_report, task_dependency_report
from .errors import WudiTaskError
from .github_delivery import fetch_delivery
from .repository import TaskIndex
from .util import atomic_write_json, utc_now


DeliveryFetcher = Callable[[Mapping[str, Any]], dict[str, Any]]

INSTALL_CONTENT_MARKER = "<!-- WUDITASK_INSTALL_CONTENT -->"
WORKFLOW_CONTENT_MARKER = "<!-- WUDITASK_WORKFLOW_CONTENT -->"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_UNORDERED_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_ORDERED_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")
_INLINE_RE = re.compile(r"`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\n]+)\)")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+-]+$")

_COPIED_SITE_ASSETS = (
    "index.html",
    "styles.css",
    "app.js",
    "dag.html",
    "dag.js",
    "install.md",
    "workflow.md",
)
_SITE_SOURCE_FILES = (
    *_COPIED_SITE_ASSETS,
    "install.template.html",
    "workflow.template.html",
)
_GENERATED_SITE_FILES = (
    "install.html",
    "workflow.html",
    "snapshot.json",
    ".nojekyll",
    ".wuditask-site",
)


def _safe_link_target(value: str) -> bool:
    target = value.strip()
    if not target or any(ord(character) < 32 for character in target):
        return False
    parsed = urlsplit(target)
    if parsed.scheme:
        return parsed.scheme.lower() in {"http", "https"}
    return not target.startswith("//")


def _render_inline(value: str) -> str:
    rendered: list[str] = []
    cursor = 0
    for match in _INLINE_RE.finditer(value):
        rendered.append(html.escape(value[cursor : match.start()]))
        code = match.group(1)
        if code is not None:
            rendered.append(f"<code>{html.escape(code)}</code>")
        else:
            label = match.group(2) or ""
            target = (match.group(3) or "").strip()
            if _safe_link_target(target):
                rendered.append(
                    f'<a href="{html.escape(target, quote=True)}">'
                    f"{html.escape(label)}</a>"
                )
            else:
                rendered.append(html.escape(match.group(0)))
        cursor = match.end()
    rendered.append(html.escape(value[cursor:]))
    return "".join(rendered)


def _starts_block(line: str) -> bool:
    return bool(
        line.startswith("```")
        or _HEADING_RE.fullmatch(line)
        or _UNORDERED_ITEM_RE.fullmatch(line)
        or _ORDERED_ITEM_RE.fullmatch(line)
    )


def _render_markdown(markdown: str) -> str:
    """Render the deliberately small, raw-HTML-free document subset."""

    lines = markdown.splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        if line.startswith("```"):
            language = line[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            language_class = (
                f' class="language-{language}"'
                if language and _LANGUAGE_RE.fullmatch(language)
                else ""
            )
            code = html.escape("\n".join(code_lines))
            rendered.append(f"<pre><code{language_class}>{code}</code></pre>")
            continue

        heading = _HEADING_RE.fullmatch(line)
        if heading:
            level = len(heading.group(1))
            rendered.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        unordered = _UNORDERED_ITEM_RE.fullmatch(line)
        if unordered:
            items = []
            while index < len(lines):
                item = _UNORDERED_ITEM_RE.fullmatch(lines[index])
                if item is None:
                    break
                items.append(f"<li>{_render_inline(item.group(1))}</li>")
                index += 1
            rendered.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue

        ordered = _ORDERED_ITEM_RE.fullmatch(line)
        if ordered:
            items = []
            while index < len(lines):
                item = _ORDERED_ITEM_RE.fullmatch(lines[index])
                if item is None:
                    break
                items.append(f"<li>{_render_inline(item.group(1))}</li>")
                index += 1
            rendered.append("<ol>\n" + "\n".join(items) + "\n</ol>")
            continue

        paragraph = [line.strip()]
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not _starts_block(lines[index])
        ):
            paragraph.append(lines[index].strip())
            index += 1
        rendered.append(f"<p>{_render_inline(' '.join(paragraph))}</p>")

    return "\n".join(rendered)


def _render_markdown_page(
    source: Path,
    *,
    template_name: str,
    markdown_name: str,
    marker: str,
    error_code: str,
    page_name: str,
) -> str:
    template = (source / template_name).read_text(encoding="utf-8")
    marker_count = template.count(marker)
    if marker_count != 1:
        raise WudiTaskError(
            error_code,
            f"{page_name} page template must contain exactly one content marker.",
            details={
                "marker": marker,
                "marker_count": marker_count,
            },
        )
    markdown = (source / markdown_name).read_text(encoding="utf-8")
    return template.replace(marker, _render_markdown(markdown))


def _render_install_page(source: Path) -> str:
    return _render_markdown_page(
        source,
        template_name="install.template.html",
        markdown_name="install.md",
        marker=INSTALL_CONTENT_MARKER,
        error_code="site_install_template_invalid",
        page_name="Install",
    )


def _render_workflow_page(source: Path) -> str:
    return _render_markdown_page(
        source,
        template_name="workflow.template.html",
        markdown_name="workflow.md",
        marker=WORKFLOW_CONTENT_MARKER,
        error_code="site_workflow_template_invalid",
        page_name="Workflow",
    )


def _public_snapshot_value(value: Any) -> Any:
    """Copy Hub data for Pages without publishing per-run identifiers."""

    if isinstance(value, Mapping):
        public: dict[str, Any] = {}
        for key, item in value.items():
            if key == "run_id":
                continue
            if key in {"active_agents", "participants"} and isinstance(item, list):
                public[key] = [
                    {"login": agent["login"]}
                    for agent in item
                    if isinstance(agent, Mapping)
                    and isinstance(agent.get("login"), str)
                ]
                continue
            public[key] = _public_snapshot_value(item)
        return public
    if isinstance(value, list):
        return [_public_snapshot_value(item) for item in value]
    return value


def build_snapshot(
    index: TaskIndex,
    *,
    hub_repo: str | None = None,
    delivery_fetcher: DeliveryFetcher | None = None,
) -> dict[str, Any]:
    fetch = delivery_fetcher or fetch_delivery
    open_report = dependency_report(index)
    report_by_id = {task["id"]: task for task in open_report["tasks"]}
    open_tasks = []
    for record in sorted(
        index.open.values(),
        key=lambda item: (
            item.task["priority"],
            item.task["created_at"],
            item.task["id"],
        ),
    ):
        task = record.task
        open_tasks.append(
            _public_snapshot_value(
                {
                    **task,
                    "derived": report_by_id[task["id"]],
                    "delivery": fetch(task["source"]),
                }
            )
        )
    archived_tasks = [
        _public_snapshot_value(
            {
                **record.task,
                "derived": task_dependency_report(record, index),
                "delivery": fetch(record.task["source"]),
            }
        )
        for record in sorted(
            index.archived.values(),
            key=lambda item: (
                item.task["completion"]["completed_at"],
                item.task["id"],
            ),
            reverse=True,
        )
    ]
    outcomes: dict[str, int] = {"done": 0, "failed": 0, "cancelled": 0}
    for task in archived_tasks:
        outcome = task["completion"]["outcome"]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    repos = sorted({task["repo"] for task in open_tasks + archived_tasks})
    return {
        "schema_version": 3,
        "generated_at": utc_now(),
        "hub_repo": hub_repo,
        "counts": {
            **open_report["summary"],
            "archived": len(archived_tasks),
            "outcomes": outcomes,
        },
        "repositories": repos,
        "open_tasks": open_tasks,
        "archived_tasks": archived_tasks,
    }


def build_site(
    index: TaskIndex,
    *,
    source: Path,
    output: Path,
    hub_repo: str | None = None,
    delivery_fetcher: DeliveryFetcher | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output == source or output == source.parent:
        raise WudiTaskError(
            "unsafe_site_output",
            "Site output must not overwrite the source directory or repository root.",
            details={"output": str(output)},
        )
    missing = [name for name in _SITE_SOURCE_FILES if not (source / name).is_file()]
    if missing:
        raise WudiTaskError(
            "site_source_missing",
            "Static site source is incomplete.",
            details={"missing": missing, "source": str(source)},
        )
    install_page = _render_install_page(source)
    workflow_page = _render_workflow_page(source)
    generated_names = {*_COPIED_SITE_ASSETS, *_GENERATED_SITE_FILES}
    if output.exists() and not output.is_dir():
        raise WudiTaskError(
            "site_output_not_directory",
            "Site output path exists and is not a directory.",
            details={"output": str(output)},
        )
    if output.exists():
        existing_names = {entry.name for entry in output.iterdir()}
        unexpected = sorted(existing_names - generated_names)
        if unexpected:
            raise WudiTaskError(
                "site_output_not_owned",
                "Refusing to clear a directory that contains non-WudiTask files.",
                details={"output": str(output), "unexpected": unexpected},
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in _COPIED_SITE_ASSETS:
        shutil.copy2(source / name, output / name)
    (output / "install.html").write_text(
        install_page,
        encoding="utf-8",
    )
    (output / "workflow.html").write_text(
        workflow_page,
        encoding="utf-8",
    )
    snapshot = build_snapshot(
        index,
        hub_repo=hub_repo,
        delivery_fetcher=delivery_fetcher,
    )
    atomic_write_json(output / "snapshot.json", snapshot)
    (output / ".nojekyll").touch()
    (output / ".wuditask-site").touch()
    return {
        "message": f"Built WudiTask dashboard at {output}.",
        "output": str(output),
        "files": [*_COPIED_SITE_ASSETS, *_GENERATED_SITE_FILES],
        "counts": snapshot["counts"],
    }
