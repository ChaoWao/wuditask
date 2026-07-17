from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .dependencies import dependency_report, task_dependency_report
from .errors import WudiTaskError
from .github_delivery import fetch_delivery
from .repository import TaskIndex
from .util import atomic_write_json, utc_now


DeliveryFetcher = Callable[[Mapping[str, Any]], dict[str, Any]]


def build_snapshot(
    index: TaskIndex,
    *,
    hub_repo: str | None = None,
    delivery_fetcher: DeliveryFetcher = fetch_delivery,
) -> dict[str, Any]:
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
            {
                **task,
                "derived": report_by_id[task["id"]],
                "delivery": delivery_fetcher(task["source"]),
            }
        )
    archived_tasks = [
        {
            **record.task,
            "derived": task_dependency_report(record, index),
            "delivery": delivery_fetcher(record.task["source"]),
        }
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
        "schema_version": 2,
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
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output == source or output == source.parent:
        raise WudiTaskError(
            "unsafe_site_output",
            "Site output must not overwrite the source directory or repository root.",
            details={"output": str(output)},
        )
    required = ("index.html", "styles.css", "app.js")
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise WudiTaskError(
            "site_source_missing",
            "Static site source is incomplete.",
            details={"missing": missing, "source": str(source)},
        )
    generated_names = {
        *required,
        "snapshot.json",
        ".nojekyll",
        ".wuditask-site",
    }
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
    for name in required:
        shutil.copy2(source / name, output / name)
    snapshot = build_snapshot(index, hub_repo=hub_repo)
    atomic_write_json(output / "snapshot.json", snapshot)
    (output / ".nojekyll").touch()
    (output / ".wuditask-site").touch()
    return {
        "message": f"Built WudiTask dashboard at {output}.",
        "output": str(output),
        "files": [*required, "snapshot.json", ".nojekyll", ".wuditask-site"],
        "counts": snapshot["counts"],
    }
