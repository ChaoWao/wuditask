from __future__ import annotations

from typing import Any

from .dependencies import task_dependency_report
from .errors import DataValidationError
from .repository import TaskRepository


def validate_repository(repository: TaskRepository) -> dict[str, Any]:
    issues = repository.validation_issues()
    if issues:
        raise DataValidationError(issues)
    index = repository.load_index()
    reports = [task_dependency_report(record, index) for record in index.all.values()]
    semantic_issues: list[dict[str, str]] = []
    for report in reports:
        if report["cycle"]:
            semantic_issues.append(
                {
                    "path": report["id"],
                    "message": f"dependency cycle: {' -> '.join(report['cycle'])}",
                }
            )
        for dependency in report["dependencies"]:
            if not dependency["exists"]:
                semantic_issues.append(
                    {
                        "path": report["id"],
                        "message": f"missing dependency {dependency['id']}",
                    }
                )
    if semantic_issues:
        raise DataValidationError(semantic_issues)
    return {
        "message": "All task data and dependency references are valid.",
        "open": len(index.open),
        "archived": len(index.archived),
        "deletions": len(repository.load_deletion_receipts()),
    }
