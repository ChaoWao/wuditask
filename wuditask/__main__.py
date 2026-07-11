from pathlib import Path

from .cli import main


raise SystemExit(main(default_tool=Path(__file__).resolve().parents[1]))
