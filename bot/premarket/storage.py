from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

PREFIX = "premarket-brief"


def premarket_briefs_dir(root: Path) -> Path:
    p = (root / "data" / "premarket_briefs").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def brief_paths_for_day(root: Path, day: date) -> tuple[Path, Path]:
    d = premarket_briefs_dir(root)
    stem = f"{day.isoformat()}-{PREFIX}"
    return d / f"{stem}.json", d / f"{stem}.md"


def find_latest_premarket_brief(root: Path) -> dict[str, Any] | None:
    d = premarket_briefs_dir(root)
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"*-{PREFIX}.json"), reverse=True)
    for f in files:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None
