"""One-time agent setup without migrating the old business/task branch."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .coordination import ExecutionRepository, require
from .execution_cli import _configure, coordinator, personal_root
from .gitops import _file_lock
from .install import _same_repository, install_agent_access
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
    probe = subprocess.run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "--",
            remote,
            f"refs/heads/{branch}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    require(
        probe.returncode in {0, 2},
        "execution_remote_unavailable",
        "Cannot inspect the execution remote; no branch was created.",
    )
    created = False
    if probe.returncode == 2:
        with tempfile.TemporaryDirectory(prefix="wuditask-setup-") as directory:
            root = Path(directory)
            coord._run(["git", "init", "-b", branch], cwd=root)
            ExecutionRepository(root).initialize(
                actor, "Fresh isolated branch; no legacy records migrated."
            )
            coord._run(
                ["git", "add", "data", "hub.json", "coordination.json"], cwd=root
            )
            coord._run(
                [
                    "git",
                    "-c",
                    f"user.name={actor['login']}",
                    "-c",
                    f"user.email={actor['login']}@users.noreply.github.com",
                    "commit",
                    "-m",
                    "Initialize personal execution storage",
                ],
                cwd=root,
            )
            pushed = coord._push(root)
            created = pushed.returncode == 0
            # A concurrent setup or a lost push response is resolved by reading
            # the branch. Never force the local seed over an existing remote.
    with coord.snapshot() as repository:
        inventory = repository.inventory()
        require(
            inventory["initialized"],
            "legacy_branch_selected",
            "This branch contains legacy data. Use a separate branch; setup does not migrate it.",
        )
    return {"branch_created": created, "remote": remote, "branch": branch}


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
        "Use a separate execution repository, not the tool repository.",
    )
    with _file_lock(root / "setup.lock"):
        config_path = root / "config.json"
        if config_path.exists():
            config = read_json(config_path)
            require(
                all(
                    config.get(k) == v
                    for k, v in {
                        "actor_id": actor["actor_id"],
                        "remote": args.remote,
                        "branch": args.branch,
                    }.items()
                ),
                "coordination_already_configured",
                "This environment is already bound to another authority/account; do not silently switch it.",
            )
        result = prepare_branch(args.remote, args.branch, actor)
        result["configuration"] = _configure(args, actor)
        result["installation"] = install_agent_access(
            tool_root, hub_remote=args.remote, hub_branch=args.branch
        )
        result["skill"] = "$wuditask-coordinate"
        return result
