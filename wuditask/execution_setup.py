"""Upgrade the original execution branch and register the single skill."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .coordination import require
from .execution_cli import _configure, coordinator, personal_root, journal_write
from .gitops import _file_lock
from .install import _same_repository, install_agent_access
from .model import Identity
from .util import read_json


def prepare_branch(remote: str, branch: str, actor: dict[str, str]) -> dict[str, Any]:
    parsed = urlsplit(remote)
    require(
        not (
            parsed.scheme in {"http", "https"} and (parsed.username or parsed.password)
        ),
        "credential_in_remote",
        "Keep credentials in SSH or Git credential storage.",
    )
    coord = coordinator({"remote": remote, "branch": branch})
    # snapshot/write require the existing branch. No orphan or alternate branch is made.
    result = coord.write(
        lambda repo: repo.upgrade_in_place(actor),
        Identity(actor["login"]),
        lambda _: "Upgrade execution storage on the original branch",
    )
    return {
        "branch_created": False,
        "remote": remote,
        "branch": branch,
        "storage": result,
    }


def setup(
    args: argparse.Namespace, actor: dict[str, str], tool_root: Path
) -> dict[str, Any]:
    root = personal_root()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=tool_root,
        capture_output=True,
        text=True,
        check=False,
    )
    require(
        origin.returncode == 0
        and not _same_repository(origin.stdout.strip(), args.remote),
        "invalid_execution_remote",
        "Execution storage must differ from the tool repository.",
    )
    with _file_lock(root / "setup.lock"):
        path = root / "config.json"
        config = read_json(path) if path.exists() else None
        switching = bool(
            config
            and (config["remote"] != args.remote or config["branch"] != args.branch)
        )
        if config:
            require(
                config["actor_id"] == actor["actor_id"],
                "coordination_account_changed",
                "Upgrade with the configured GitHub account.",
            )
        if switching:
            require(
                args.coord_command == "upgrade"
                and _same_repository(config["remote"], args.remote),
                "coordination_already_configured",
                "Use explicit upgrade within the same repository; do not silently bypass claims.",
            )
            if config["branch"] != args.branch:
                with coordinator(config).snapshot() as source:
                    source.check_relocation(
                        {"remote": args.remote, "branch": args.branch}
                    )
        result = prepare_branch(args.remote, args.branch, actor)
        if switching and config["branch"] != args.branch:
            result["source_retired"] = coordinator(config).write(
                lambda repo: repo.retire_empty_branch(
                    {"remote": args.remote, "branch": args.branch}
                ),
                Identity(actor["login"]),
                lambda _: "Retire empty alternate execution branch",
            )
        # Preflight/link reconciliation happens before changing personal authority.
        # Retrying after a partially completed install is safe.
        result["installation"] = install_agent_access(
            tool_root, hub_remote=args.remote, hub_branch=args.branch
        )
        if switching:
            with _file_lock(root / "config.lock"):
                require(
                    read_json(path) == config,
                    "configuration_changed",
                    "Local coordination configuration changed during upgrade.",
                )
                journal_write(root / "configuration-before-upgrade.json", config)
                journal_write(
                    path, {**config, "remote": args.remote, "branch": args.branch}
                )
            result["configuration"] = {"configured": True, "changed": True}
        else:
            result["configuration"] = _configure(args, actor)
        result["skill"] = "$wuditask"
        return result


def upgrade(
    args: argparse.Namespace, actor: dict[str, str], tool_root: Path
) -> dict[str, Any]:
    installed = Path.home() / ".wuditask/config.json"
    config = read_json(installed) if installed.exists() else {}
    remote = args.remote or config.get("hub_remote")
    branch = args.branch or config.get("hub_branch", "main")
    # The short-lived separate-branch setup is folded back into the original main.
    if not args.branch and branch == "personal-execution":
        branch = "main"
    require(
        bool(remote),
        "execution_remote_required",
        "Specify the original execution repository with --remote.",
    )
    args.remote, args.branch = remote, branch
    return setup(args, actor, tool_root)
