from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .configuration import CONFIG_SCHEMA_VERSION, config_path
from .errors import WudiTaskError
from .gitops import GitCoordinator, default_cache_root
from .util import atomic_write_json, repo_from_remote, utc_now
from .validation import validate_repository


REQUIRED_SKILL_NAMES = {
    "wuditask-add",
    "wuditask-archive",
    "wuditask-assign",
    "wuditask-check",
    "wuditask-delete",
    "wuditask-execute",
    "wuditask-install",
    "wuditask-list",
    "wuditask-release",
    "wuditask-selfupdate",
    "wuditask-show",
    "wuditask-unassign",
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


def _same_repository(first: str, second: str) -> bool:
    if first.strip().rstrip("/").removesuffix(".git") == second.strip().rstrip(
        "/"
    ).removesuffix(".git"):
        return True
    first_repo = repo_from_remote(first)
    second_repo = repo_from_remote(second)
    return (
        first_repo is not None
        and second_repo is not None
        and first_repo.lower() == second_repo.lower()
    )


def _path_exists_error(destination: Path) -> WudiTaskError:
    return WudiTaskError(
        "install_path_exists",
        f"Install destination already exists: {destination}",
        details={
            "path": str(destination),
            "action": "Inspect the path. Use --replace only for a conflicting install destination that should be preserved as a backup.",
        },
    )


def _link(
    source: Path,
    destination: Path,
    *,
    replace: bool,
    safe_targets: set[Path] | None = None,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = source.resolve()
    if destination.is_symlink() and destination.resolve() == source:
        return {"path": str(destination), "target": str(source), "changed": False}
    backup = None
    if os.path.lexists(destination):
        if _symlink_target(destination) in (safe_targets or set()):
            destination.unlink()
        else:
            if not replace:
                raise _path_exists_error(destination)
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


def _registered_tool(path: Path) -> Path | None:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if (
        not isinstance(config, dict)
        or config.get("schema_version") != CONFIG_SCHEMA_VERSION
    ):
        return None
    value = config.get("tool_path")
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    return candidate.resolve(strict=False)


def _preflight_parent(destination: Path) -> None:
    ancestor = destination.parent
    while not os.path.lexists(ancestor):
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent
    if not ancestor.is_dir():
        raise _path_exists_error(ancestor)


def _preflight_link(
    source: Path,
    destination: Path,
    *,
    replace: bool,
    safe_targets: set[Path],
) -> None:
    _preflight_parent(destination)
    source = source.resolve()
    if destination.is_symlink() and _symlink_target(destination) == source:
        return
    if not os.path.lexists(destination):
        return
    if _symlink_target(destination) in safe_targets:
        return
    if not replace:
        raise _path_exists_error(destination)


def _remove_stale_skill_links(
    registered_skills_roots: set[Path],
    product_path: Path,
    skill_names: set[str],
) -> list[dict[str, str]]:
    if not product_path.is_dir():
        return []
    removed = []
    for destination in product_path.iterdir():
        target = _symlink_target(destination)
        if (
            target is None
            or target.parent not in registered_skills_roots
            or target.name != destination.name
            or destination.name in skill_names
        ):
            continue
        destination.unlink()
        removed.append({"path": str(destination), "target": str(target)})
    return removed


def install_agent_access(
    tool_root: Path,
    *,
    hub_remote: str,
    hub_branch: str = "main",
    home: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    tool_root = tool_root.resolve()
    cache_root = (
        default_cache_root(home=home) if home is not None else default_cache_root()
    )
    home = (home or Path.home()).resolve()
    hub_remote = hub_remote.strip()
    hub_branch = hub_branch.strip()
    if not hub_remote or not hub_branch:
        raise WudiTaskError(
            "invalid_hub_remote",
            "Task Hub remote and branch must both be non-empty.",
        )
    tool = tool_root / "tools" / "wuditask.py"
    skills_root = tool_root / ".agents" / "skills"
    if not tool.is_file():
        raise WudiTaskError(
            "invalid_tool_clone",
            "WudiTask tool clone is missing tools/wuditask.py.",
            details={"tool_path": str(tool_root)},
        )
    if not skills_root.is_dir():
        raise WudiTaskError(
            "invalid_tool_clone",
            "WudiTask tool clone is missing .agents/skills.",
            details={"tool_path": str(tool_root)},
        )

    tool_remote = _git_value(tool_root, "remote", "get-url", "origin")
    tool_branch = _git_value(tool_root, "branch", "--show-current")
    if not tool_remote or not tool_branch:
        raise WudiTaskError(
            "invalid_tool_clone",
            "WudiTask tool clone requires an origin remote and a checked-out branch.",
            details={"tool_path": str(tool_root)},
        )
    if _same_repository(tool_remote, hub_remote):
        raise WudiTaskError(
            "hub_matches_tool_repository",
            "The task Hub must be a different Git repository from the WudiTask tool.",
            details={
                "tool_remote": tool_remote,
                "hub_remote": hub_remote,
            },
        )

    skill_names = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and (path / "SKILL.md").is_file()
    }
    missing_skill_names = sorted(REQUIRED_SKILL_NAMES - skill_names)
    unexpected_skill_names = sorted(skill_names - REQUIRED_SKILL_NAMES)
    if missing_skill_names or unexpected_skill_names:
        raise WudiTaskError(
            "invalid_tool_clone",
            "WudiTask tool clone does not contain the exact required agent skill set.",
            details={
                "tool_path": str(tool_root),
                "missing_skills": missing_skill_names,
                "unexpected_skills": unexpected_skill_names,
            },
        )
    skills = [skills_root / name for name in sorted(REQUIRED_SKILL_NAMES)]

    coordinator = GitCoordinator(
        remote=hub_remote,
        branch=hub_branch,
        cache_root=cache_root,
    )
    with coordinator.snapshot() as repository:
        hub_validation = validate_repository(repository)

    destination_config = config_path(home)
    _preflight_parent(destination_config)
    registered_tools = {tool_root}
    previous_tool = _registered_tool(destination_config)
    if previous_tool is not None:
        registered_tools.add(previous_tool)
    registered_skills_roots = {
        (registered_tool / ".agents" / "skills").resolve(strict=False)
        for registered_tool in registered_tools
    }
    product_paths = (home / ".agents" / "skills", home / ".claude" / "skills")
    for product_path in product_paths:
        for skill in skills:
            safe_targets = {
                registered_skills_root / skill.name
                for registered_skills_root in registered_skills_roots
            }
            _preflight_link(
                skill,
                product_path / skill.name,
                replace=replace,
                safe_targets=safe_targets,
            )
    launcher_path = home / ".local" / "bin" / "wuditask"
    launcher_safe_targets = {
        (registered_tool / "tools" / "wuditask.py").resolve(strict=False)
        for registered_tool in registered_tools
    }
    _preflight_link(
        tool,
        launcher_path,
        replace=replace,
        safe_targets=launcher_safe_targets,
    )

    links = []
    removed_links = []
    for product_path in product_paths:
        removed_links.extend(
            _remove_stale_skill_links(
                registered_skills_roots,
                product_path,
                skill_names,
            )
        )
        for skill in skills:
            safe_targets = {
                registered_skills_root / skill.name
                for registered_skills_root in registered_skills_roots
            }
            links.append(
                _link(
                    skill,
                    product_path / skill.name,
                    replace=replace,
                    safe_targets=safe_targets,
                )
            )
    launcher = _link(
        tool,
        launcher_path,
        replace=replace,
        safe_targets=launcher_safe_targets,
    )
    links.append(launcher)

    config = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "tool_path": str(tool_root),
        "tool_remote": tool_remote,
        "tool_branch": tool_branch,
        "hub_remote": hub_remote,
        "hub_branch": hub_branch,
        "installed_at": utc_now(),
    }
    atomic_write_json(destination_config, config)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    launcher_on_path = str((home / ".local" / "bin").resolve()) in {
        str(Path(entry).expanduser().resolve()) for entry in path_entries if entry
    }
    return {
        "message": f"Registered WudiTask tool at {tool_root} with task Hub {hub_remote}.",
        "config": str(destination_config),
        "tool_path": str(tool_root),
        "tool_remote": tool_remote,
        "tool_branch": tool_branch,
        "hub_remote": hub_remote,
        "hub_branch": hub_branch,
        "hub_cache": str(coordinator.cache_path),
        "hub_validation": hub_validation,
        "skills": [skill.name for skill in skills],
        "links": links,
        "removed_links": removed_links,
        "launcher": str(home / ".local" / "bin" / "wuditask"),
        "launcher_on_path": launcher_on_path,
    }
