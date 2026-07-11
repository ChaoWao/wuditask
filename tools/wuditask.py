#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT))

from wuditask.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(default_tool=TOOL_ROOT))
