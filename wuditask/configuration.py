from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import WudiTaskError
from .util import is_utc_timestamp


CONFIG_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class WudiTaskConfig:
    tool_path: Path
    tool_remote: str
    tool_branch: str
    hub_remote: str
    hub_branch: str


def config_path(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / ".wuditask" / "config.json"


def load_config(
    *,
    home: Path | None = None,
    expected_tool_path: Path | None = None,
) -> WudiTaskConfig:
    path = config_path(home)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WudiTaskError(
            "wuditask_config_missing",
            "WudiTask is not installed for this user.",
            details={
                "config": str(path),
                "action": "Run the wuditask-install skill with the task Hub remote.",
            },
        ) from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask configuration is not valid JSON.",
            details={"config": str(path)},
        ) from exc

    required = {
        "schema_version",
        "tool_path",
        "tool_remote",
        "tool_branch",
        "hub_remote",
        "hub_branch",
        "installed_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask configuration does not match the current two-repository contract.",
            details={
                "config": str(path),
                "required_fields": sorted(required),
                "action": "Run the wuditask-install skill again; legacy hub_path configuration is not supported.",
            },
        )
    if value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise WudiTaskError(
            "wuditask_config_version_mismatch",
            f"WudiTask configuration schema must be {CONFIG_SCHEMA_VERSION}.",
            details={
                "config": str(path),
                "actual": value.get("schema_version"),
                "expected": CONFIG_SCHEMA_VERSION,
                "action": "Run the wuditask-install skill again.",
            },
        )
    if not is_utc_timestamp(value.get("installed_at")):
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask configuration has an invalid installed_at timestamp.",
            details={"config": str(path)},
        )

    tool_value = value.get("tool_path")
    if not isinstance(tool_value, str) or not tool_value.strip():
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask configuration has an invalid tool_path.",
            details={"config": str(path)},
        )
    tool_path = Path(tool_value).expanduser()
    if not tool_path.is_absolute():
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask tool_path must be absolute.",
            details={"config": str(path), "tool_path": tool_value},
        )
    tool_path = tool_path.resolve(strict=False)

    text_fields = ("tool_remote", "tool_branch", "hub_remote", "hub_branch")
    invalid_fields = [
        field
        for field in text_fields
        if not isinstance(value.get(field), str) or not value[field].strip()
    ]
    if invalid_fields:
        raise WudiTaskError(
            "wuditask_config_invalid",
            "WudiTask configuration contains empty repository settings.",
            details={"config": str(path), "fields": invalid_fields},
        )

    if expected_tool_path is not None:
        expected = expected_tool_path.expanduser().resolve()
        if tool_path != expected:
            raise WudiTaskError(
                "wuditask_tool_registration_mismatch",
                "This WudiTask clone is not the registered tool installation.",
                details={
                    "registered_tool_path": str(tool_path),
                    "invoked_tool_path": str(expected),
                    "action": "Invoke the registered tool or run the wuditask-install skill for this clone.",
                },
            )

    return WudiTaskConfig(
        tool_path=tool_path,
        tool_remote=value["tool_remote"].strip(),
        tool_branch=value["tool_branch"].strip(),
        hub_remote=value["hub_remote"].strip(),
        hub_branch=value["hub_branch"].strip(),
    )
