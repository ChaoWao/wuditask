"""Update code first, then run storage upgrade in a fresh interpreter."""

from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from .errors import WudiTaskError
from .selfupdate import self_update


def upgrade_installation(tool_root: Path) -> dict:
    update = self_update(tool_root)
    process = subprocess.run(
        [
            sys.executable,
            str(tool_root / "tools/wuditask.py"),
            "--json",
            "coord",
            "upgrade",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise WudiTaskError(
            "upgrade_incomplete",
            "Tool updated; execution upgrade needs attention. Retry upgrade after resolving the reported error.",
            details={
                "tool_update": update,
                "stdout": process.stdout,
                "stderr": process.stderr,
            },
        )
    return {"tool_update": update, "coordination": json.loads(process.stdout)}
