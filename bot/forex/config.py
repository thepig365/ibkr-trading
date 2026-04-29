"""Load ``config/forex_ict_1m.yaml`` (loose dict; no Pydantic yet)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_RELPATH = "config/forex_ict_1m.yaml"


def load_forex_ict_config(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    p = root / DEFAULT_RELPATH
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def melbourne_session_open(cfg: dict[str, Any], *, now_tz: Any) -> tuple[bool, str]:
    """Return (in_window, hhmm-local) — uses session.timezone/window."""

    sess = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
    tz_name = str(sess.get("timezone") or "Australia/Melbourne")

    win = str(sess.get("window") or "00:00-23:59")
    try:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(tz_name)
    except Exception:
        return True, ""

    lp = win.split("-", 1)
    sh, sm = (lp[0].strip().split(":") + ["0"])[:2]
    eh, em = (lp[1].strip().split(":") + ["0"])[:2] if len(lp) > 1 else ("23", "59")
    start_m = int(sh) * 60 + int(sm)
    end_m = int(eh) * 60 + int(em)

    n = now_tz.astimezone(z)
    m = n.hour * 60 + n.minute
    if start_m <= end_m:
        ok = start_m <= m <= end_m
    else:
        ok = m >= start_m or m <= end_m
    w = (cfg.get("session") or {}).get("days")
    dow = n.strftime("%a")
    if isinstance(w, list) and w:
        wl = [str(x).strip()[:3] for x in w]
        if dow not in wl:
            ok = False
    return ok, n.strftime("%H:%M")


__all__ = ["load_forex_ict_config", "melbourne_session_open", "DEFAULT_RELPATH"]
