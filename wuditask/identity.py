from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .errors import WudiTaskError
from .model import Identity
from .util import repo_from_remote


def _parse_actor(value: str) -> Identity:
    login = value.strip()
    if not login or ":" in login:
        raise WudiTaskError(
            "invalid_actor",
            "Actor override must be a GitHub login.",
            details={"value": value},
        )
    return Identity(login=login)


def resolve_identity(actor_override: str | None = None) -> Identity:
    override = actor_override or os.environ.get("WUDITASK_ACTOR")
    if override:
        return _parse_actor(override)
    if shutil.which("gh") is None:
        raise WudiTaskError(
            "gh_not_found",
            "GitHub CLI is required to identify the human actor.",
            details={"action": "Install gh and run gh auth login, then retry."},
        )
    process = subprocess.run(
        ["gh", "api", "user"],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise WudiTaskError(
            "gh_identity_failed",
            "Could not read the authenticated GitHub identity.",
            details={
                "stderr": process.stderr.strip(),
                "action": "Run gh auth login and retry.",
            },
        )
    try:
        payload = json.loads(process.stdout)
        login = payload["login"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WudiTaskError(
            "gh_identity_invalid",
            "GitHub CLI returned an invalid user record.",
        ) from exc
    if not isinstance(login, str) or not login.strip():
        raise WudiTaskError(
            "gh_identity_invalid", "GitHub CLI returned an invalid user record."
        )
    return Identity(login=login)


def detect_current_repo(cwd: Path | None = None) -> str | None:
    process = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=cwd or Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        return None
    return repo_from_remote(process.stdout.strip())
