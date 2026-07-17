from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import WudiTaskError

TASK_ID_RE = re.compile(r"^WDT-\d{8}T\d{6}Z-[0-9A-F]{6}$")
RUN_ID_RE = re.compile(r"^WDX-[0-9A-F]{24}$")
DELETION_RECEIPT_ID_RE = re.compile(r"^WDR-[0-9A-F]{24}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def new_task_id(now: str | None = None) -> str:
    stamp = (now or utc_now()).replace("-", "").replace(":", "")
    return f"WDT-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def new_run_id() -> str:
    return f"WDX-{uuid.uuid4().hex[:24].upper()}"


def deletion_receipt_id(
    task_ids: list[str],
    reason: str,
    login: str,
) -> str:
    payload = json.dumps(
        {
            "login": login.strip().casefold(),
            "reason": reason.strip(),
            "task_ids": sorted(task_ids),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24].upper()
    return f"WDR-{digest}"


def timestamp_from_task_id(task_id: str) -> str | None:
    if not TASK_ID_RE.fullmatch(task_id):
        return None
    compact = task_id.removeprefix("WDT-").split("-", 1)[0]
    try:
        parsed = datetime.strptime(compact, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise WudiTaskError("file_not_found", f"File does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WudiTaskError(
            "invalid_json",
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}.",
        ) from exc


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_repo(value: str) -> str:
    candidate = value.strip().removesuffix(".git")
    if not REPO_RE.fullmatch(candidate):
        raise WudiTaskError(
            "invalid_repository",
            "Repository must use the GitHub owner/name form.",
            details={"value": value},
        )
    return candidate


def repo_from_remote(remote: str) -> str | None:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        return normalize_repo(value.split(":", 1)[1])
    if value.startswith("ssh://git@github.com/"):
        return normalize_repo(urlparse(value).path.lstrip("/"))
    parsed = urlparse(value)
    if parsed.hostname == "github.com":
        return normalize_repo(parsed.path.lstrip("/"))
    return None


def is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True
