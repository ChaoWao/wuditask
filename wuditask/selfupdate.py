from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .configuration import load_config
from .errors import WudiTaskError


def _run(
    command: list[str],
    *,
    cwd: Path,
    allowed: set[int] | None = {0},
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise WudiTaskError(
            "selfupdate_command_timeout",
            f"Self-update command timed out: {' '.join(command)}",
            details={"timeout_seconds": timeout},
            exit_code=4,
        ) from exc
    if allowed is not None and process.returncode not in allowed:
        raise WudiTaskError(
            "selfupdate_command_failed",
            f"Self-update command failed: {' '.join(command)}",
            details={
                "returncode": process.returncode,
                "stdout": process.stdout.strip(),
                "stderr": process.stderr.strip(),
            },
            exit_code=4,
        )
    return process


def _git_value(root: Path, *arguments: str) -> str:
    value = _run(["git", *arguments], cwd=root).stdout.strip()
    if not value:
        raise WudiTaskError(
            "selfupdate_git_state_invalid",
            f"Git returned no value for: git {' '.join(arguments)}",
            exit_code=4,
        )
    return value


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    process = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        allowed={0, 1},
    )
    return process.returncode == 0


def _worktree_changes(root: Path) -> list[str]:
    output = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
    ).stdout
    return [line for line in output.splitlines() if line]


def _skill_inventory(root: Path, revision: str) -> list[str]:
    output = _run(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            revision,
            "--",
            ".agents/skills",
        ],
        cwd=root,
    ).stdout
    names = set()
    for line in output.splitlines():
        parts = Path(line).parts
        if (
            len(parts) == 4
            and parts[:2] == (".agents", "skills")
            and not parts[2].startswith(".")
            and parts[-1] == "SKILL.md"
        ):
            names.add(parts[2])
    return sorted(names)


def _symlink_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _skill_links_need_reinstall(root: Path, revision: str, home: Path) -> bool:
    skills_root = (root / ".agents" / "skills").resolve()
    expected_names = set(_skill_inventory(root, revision))
    for product_path in (home / ".agents" / "skills", home / ".claude" / "skills"):
        for name in expected_names:
            destination = product_path / name
            if _symlink_target(destination) != (skills_root / name).resolve():
                return True
        if not product_path.is_dir():
            continue
        for destination in product_path.iterdir():
            target = _symlink_target(destination)
            if (
                target is not None
                and target.parent == skills_root
                and target.name == destination.name
                and destination.name not in expected_names
            ):
                return True
    return False


def _candidate_verification(checkout: Path) -> dict[str, Any]:
    tests = checkout / "tests"
    if not (checkout / "tools" / "wuditask.py").is_file() or not tests.is_dir():
        raise WudiTaskError(
            "selfupdate_candidate_invalid",
            "The candidate does not contain the WudiTask CLI and test suite.",
            details={"checkout": str(checkout)},
            exit_code=4,
        )
    test = _run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
        ],
        cwd=checkout,
        allowed=None,
    )
    if test.returncode != 0:
        raise WudiTaskError(
            "selfupdate_candidate_failed",
            "Candidate tests failed; the installed clone was not changed.",
            details={
                "step": "tests",
                "stdout": test.stdout.strip(),
                "stderr": test.stderr.strip(),
            },
            exit_code=4,
        )
    summary_lines = (test.stderr or test.stdout).strip().splitlines()
    return {
        "tests": "passed",
        "test_summary": summary_lines[-1] if summary_lines else "passed",
    }


def self_update(
    tool_root: Path,
    *,
    check_only: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    root = tool_root.expanduser().resolve()
    home = (home or Path.home()).expanduser().resolve()
    config = load_config(home=home, expected_tool_path=root)
    top_level = Path(_git_value(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise WudiTaskError(
            "selfupdate_invalid_tool",
            "The tool path is not the root of its Git repository.",
            details={"tool_path": str(root), "git_root": str(top_level)},
        )
    branch = _git_value(root, "branch", "--show-current")
    remote = _git_value(root, "remote", "get-url", "origin")
    if config.tool_remote != remote or config.tool_branch != branch:
        raise WudiTaskError(
            "selfupdate_tool_config_mismatch",
            "The tool clone Git state does not match the registered update source.",
            details={
                "configured_remote": config.tool_remote,
                "actual_remote": remote,
                "configured_branch": config.tool_branch,
                "actual_branch": branch,
                "action": "Switch back to the registered tool branch and remote, or run the wuditask-install skill again.",
            },
            exit_code=3,
        )
    changes = _worktree_changes(root)
    if changes and not check_only:
        raise WudiTaskError(
            "selfupdate_dirty_worktree",
            "WudiTask has local changes; refusing to overwrite or stash them.",
            details={
                "tool_path": str(root),
                "changes": changes,
                "action": "Commit, discard, or move these changes explicitly, then retry.",
            },
            exit_code=3,
        )

    _run(["git", "fetch", "--quiet", "origin", branch], cwd=root)
    local_head = _git_value(root, "rev-parse", "HEAD")
    remote_ref = f"origin/{branch}"
    remote_head = _git_value(root, "rev-parse", remote_ref)
    local_reinstall_required = _skill_links_need_reinstall(root, local_head, home)
    update_changes_skill_inventory = _skill_inventory(
        root, local_head
    ) != _skill_inventory(root, remote_head)
    commit_count = int(
        _git_value(root, "rev-list", "--count", f"{local_head}..{remote_head}")
    )
    commits = _run(
        [
            "git",
            "log",
            "--max-count=20",
            "--format=%h %s",
            f"{local_head}..{remote_head}",
        ],
        cwd=root,
    ).stdout.splitlines()

    if local_head == remote_head:
        return {
            "message": "WudiTask is already up to date.",
            "status": "up_to_date",
            "tool_path": str(root),
            "branch": branch,
            "remote": remote,
            "commit": local_head,
            "worktree_clean": not changes,
            "reinstall_required": local_reinstall_required,
            "reinstall_required_after_update": False,
        }
    if not _is_ancestor(root, local_head, remote_head):
        state = (
            "local_ahead" if _is_ancestor(root, remote_head, local_head) else "diverged"
        )
        if check_only:
            return {
                "message": f"WudiTask cannot fast-forward because the clone is {state}.",
                "status": state,
                "tool_path": str(root),
                "branch": branch,
                "remote": remote,
                "local_commit": local_head,
                "remote_commit": remote_head,
                "worktree_clean": not changes,
                "reinstall_required": local_reinstall_required,
                "reinstall_required_after_update": update_changes_skill_inventory,
            }
        raise WudiTaskError(
            f"selfupdate_{state}",
            f"WudiTask cannot fast-forward because the clone is {state}.",
            details={
                "tool_path": str(root),
                "branch": branch,
                "local_commit": local_head,
                "remote_commit": remote_head,
                "action": "Resolve the local Git history explicitly; self-update will not reset or rebase it.",
            },
            exit_code=3,
        )
    if check_only:
        return {
            "message": f"WudiTask has {commit_count} update commit(s) available.",
            "status": "update_available",
            "tool_path": str(root),
            "branch": branch,
            "remote": remote,
            "local_commit": local_head,
            "remote_commit": remote_head,
            "commit_count": commit_count,
            "commits": commits,
            "worktree_clean": not changes,
            "reinstall_required": local_reinstall_required,
            "reinstall_required_after_update": (
                local_reinstall_required or update_changes_skill_inventory
            ),
        }

    verification: dict[str, Any] = {}
    candidate_head = ""
    for attempt in range(1, 4):
        with tempfile.TemporaryDirectory(prefix="wuditask-selfupdate-") as temporary:
            checkout = Path(temporary) / "tool"
            _run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--branch",
                    branch,
                    remote,
                    str(checkout),
                ],
                cwd=root,
            )
            candidate_head = _git_value(checkout, "rev-parse", "HEAD")
            verification = _candidate_verification(checkout)
        _run(["git", "fetch", "--quiet", "origin", branch], cwd=root)
        remote_head = _git_value(root, "rev-parse", remote_ref)
        if candidate_head == remote_head:
            verification["attempts"] = attempt
            break
    else:
        raise WudiTaskError(
            "selfupdate_remote_moving",
            "The remote branch changed during candidate verification.",
            details={"attempts": 3, "action": "Retry self-update."},
            exit_code=3,
        )

    if _worktree_changes(root) or _git_value(root, "rev-parse", "HEAD") != local_head:
        raise WudiTaskError(
            "selfupdate_local_changed",
            "The installed clone changed during candidate verification.",
            details={"action": "Inspect the clone and retry; no merge was attempted."},
            exit_code=3,
        )
    if not _is_ancestor(root, local_head, candidate_head):
        raise WudiTaskError(
            "selfupdate_diverged",
            "The verified candidate no longer fast-forwards the installed clone.",
            details={"local_commit": local_head, "candidate_commit": candidate_head},
            exit_code=3,
        )

    update_changes_skill_inventory = _skill_inventory(
        root, local_head
    ) != _skill_inventory(root, candidate_head)
    _run(["git", "merge", "--ff-only", candidate_head], cwd=root)
    reinstall_required = _skill_links_need_reinstall(root, candidate_head, home)
    return {
        "message": f"Updated WudiTask from {local_head[:7]} to {candidate_head[:7]}.",
        "status": "updated",
        "tool_path": str(root),
        "branch": branch,
        "remote": remote,
        "from_commit": local_head,
        "to_commit": candidate_head,
        "commit_count": commit_count,
        "commits": commits,
        "verification": verification,
        "reinstall_required": reinstall_required,
        "reinstall_required_after_update": reinstall_required,
        "skill_inventory_changed": update_changes_skill_inventory,
    }
