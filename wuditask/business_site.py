"""Publish a GitHub business graph independently of the legacy execution store."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from .errors import WudiTaskError
from .github_graph import GraphScope, GitHubReader, collect_graph
from .util import atomic_write_json
from .site_builder import _render_install_page, _render_workflow_page

_ASSETS = {
    "index.html": "github.html",
    "dag.html": "github.html",
    "github.js": "github.js",
    "github.css": "github.css",
    "styles.css": "styles.css",
    "install.md": "install.md",
    "workflow.md": "workflow.md",
}
_OUTPUTS = {
    *_ASSETS,
    "graph.json",
    "install.html",
    "workflow.html",
    ".nojekyll",
    ".wuditask-business-site",
}


def build_business_site(
    scope: GraphScope, *, source: Path, output: Path, reader: GitHubReader | None = None
) -> dict:
    source, output = source.resolve(), output.absolute()
    if (
        output.is_symlink()
        or output.resolve() == source
        or output.resolve() in source.parents
        or source in output.resolve().parents
    ):
        raise WudiTaskError(
            "unsafe_site_output", "Output must be separate from the site sources."
        )
    if output.exists():
        if (
            not output.is_dir()
            or not (output / ".wuditask-business-site").is_file()
            or set(p.name for p in output.iterdir()) - _OUTPUTS
        ):
            raise WudiTaskError(
                "site_output_not_owned",
                "Refusing to replace a non-business-site directory.",
            )
    missing = sorted(
        name for name in set(_ASSETS.values()) if not (source / name).is_file()
    )
    if missing:
        raise WudiTaskError(
            "site_source_missing",
            "Missing business site assets.",
            details={"missing": missing},
        )
    guides = {
        "install.html": _render_install_page(source),
        "workflow.html": _render_workflow_page(source),
    }
    snapshot = collect_graph(scope, reader)
    if not snapshot["available_roots"]:
        raise WudiTaskError(
            "github_scope_unavailable",
            "No public root could be read; existing output was preserved.",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".wuditask-business-", dir=output.parent))
    try:
        for destination, asset in _ASSETS.items():
            shutil.copy2(source / asset, staging / destination)
        for name, content in guides.items():
            (staging / name).write_text(content, encoding="utf-8")
        atomic_write_json(staging / "graph.json", snapshot)
        (staging / ".nojekyll").touch()
        (staging / ".wuditask-business-site").touch()
        # Pages deploys the artifact as a unit. Local rebuilds require a single writer.
        # Never erase the previous good artifact before the new generation is built.
        if output.exists():
            prior = Path(
                tempfile.mkdtemp(prefix=".wuditask-previous-", dir=output.parent)
            )
            backup = prior / "site"
            try:
                os.replace(output, backup)
                try:
                    os.replace(staging, output)
                except BaseException:
                    os.replace(backup, output)
                    raise
            except BaseException:
                # A failed rollback leaves the recoverable generation here.
                if not backup.exists():
                    prior.rmdir()
                raise
            else:
                shutil.rmtree(prior)
        else:
            os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        "message": "Built GitHub business graph; no Hub tasks were read or changed.",
        "output": str(output),
        "status": snapshot["status"],
        "nodes": len(snapshot["nodes"]),
        "edges": len(snapshot["edges"]),
        "diagnostics": snapshot["diagnostics"],
        "generated_at": snapshot["generated_at"],
    }
