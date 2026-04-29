"""Runtime enable flag for Forex auto paper (separate from stock engine)."""

from __future__ import annotations

import json
from pathlib import Path

REL = "data/runtime/forex_auto_paper_enabled.json"


def enabled_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / REL


def read_runtime_auto_enabled(project_root: Path | str) -> bool:
    p = enabled_path(Path(project_root))
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if isinstance(data, dict):
        return bool(data.get("enabled"))
    return False


def write_runtime_auto_enabled(project_root: Path | str, enabled: bool) -> Path:
    p = enabled_path(Path(project_root))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"enabled": bool(enabled)}, indent=2) + "\n", encoding="utf-8"
    )
    return p


__all__ = ["REL", "read_runtime_auto_enabled", "write_runtime_auto_enabled"]
