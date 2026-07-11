from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import WudiTaskError
from .util import atomic_write_json, utc_now


REQUIRED_SKILL_NAMES = {
    "wuditask",
    "wuditask-add",
    "wuditask-archive",
    "wuditask-dep-check",
    "wuditask-execute",
    "wuditask-inspect",
    "wuditask-install",
    "wuditask-release",
    "wuditask-selfupdate",
}


def _git_value(root: Path, *arguments: str) -> str | None:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        return None
    return process.stdout.strip() or None


def _link(source: Path, destination: Path, *, replace: bool) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = source.resolve()
    if destination.is_symlink() and destination.resolve() == source:
        return {"path": str(destination), "target": str(source), "changed": False}
    backup = None
    if os.path.lexists(destination):
        if not replace:
            raise WudiTaskError(
                "install_path_exists",
                f"Install destination already exists: {destination}",
                details={
                    "path": str(destination),
                    "action": "Inspect it, then rerun install with --replace to preserve it as a backup.",
                },
            )
        suffix = utc_now().replace("-", "").replace(":", "")
        backup = destination.with_name(f"{destination.name}.backup-{suffix}")
        destination.rename(backup)
    destination.symlink_to(source, target_is_directory=source.is_dir())
    result = {
        "path": str(destination),
        "target": str(source),
        "changed": True,
    }
    if backup is not None:
        result["backup"] = str(backup)
    return result


def _symlink_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _remove_stale_skill_links(
    skills_root: Path,
    product_path: Path,
    skill_names: set[str],
) -> list[dict[str, str]]:
    if not product_path.is_dir():
        return []
    resolved_skills_root = skills_root.resolve()
    removed = []
    for destination in product_path.iterdir():
        target = _symlink_target(destination)
        if (
            target is None
            or target.parent != resolved_skills_root
            or target.name != destination.name
            or destination.name in skill_names
        ):
            continue
        destination.unlink()
        removed.append({"path": str(destination), "target": str(target)})
    return removed


def install_agent_access(
    hub_root: Path,
    *,
    home: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    hub_root = hub_root.resolve()
    home = (home or Path.home()).resolve()
    tool = hub_root / "tools" / "wuditask.py"
    skills_root = hub_root / ".agents" / "skills"
    if not tool.is_file():
        raise WudiTaskError(
            "invalid_hub_clone",
            "WudiTask clone is missing tools/wuditask.py.",
            details={"hub_path": str(hub_root)},
        )
    if not skills_root.is_dir():
        raise WudiTaskError(
            "invalid_hub_clone",
            "WudiTask clone is missing .agents/skills.",
            details={"hub_path": str(hub_root)},
        )

    skills = sorted(
        (
            path
            for path in skills_root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and (path / "SKILL.md").is_file()
        ),
        key=lambda path: path.name,
    )
    skill_names = {path.name for path in skills}
    missing_skill_names = sorted(REQUIRED_SKILL_NAMES - skill_names)
    if missing_skill_names:
        raise WudiTaskError(
            "invalid_hub_clone",
            "WudiTask clone is missing required agent skills.",
            details={
                "hub_path": str(hub_root),
                "missing_skills": missing_skill_names,
            },
        )

    links = []
    removed_links = []
    for product_path in (home / ".agents" / "skills", home / ".claude" / "skills"):
        removed_links.extend(
            _remove_stale_skill_links(skills_root, product_path, skill_names)
        )
        for skill in skills:
            links.append(_link(skill, product_path / skill.name, replace=replace))
    launcher = _link(tool, home / ".local" / "bin" / "wuditask", replace=replace)
    links.append(launcher)

    config = {
        "schema_version": 1,
        "hub_path": str(hub_root),
        "remote": _git_value(hub_root, "remote", "get-url", "origin"),
        "branch": _git_value(hub_root, "branch", "--show-current") or "main",
        "installed_at": utc_now(),
    }
    config_path = home / ".wuditask" / "config.json"
    atomic_write_json(config_path, config)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    launcher_on_path = str((home / ".local" / "bin").resolve()) in {
        str(Path(entry).expanduser().resolve()) for entry in path_entries if entry
    }
    return {
        "message": f"Registered WudiTask clone at {hub_root}.",
        "config": str(config_path),
        "hub_path": str(hub_root),
        "skills": [skill.name for skill in skills],
        "links": links,
        "removed_links": removed_links,
        "launcher": str(home / ".local" / "bin" / "wuditask"),
        "launcher_on_path": launcher_on_path,
    }
