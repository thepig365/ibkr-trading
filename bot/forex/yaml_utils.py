"""Flatten Forex YAML (primary/secondary pairs vs legacy list)."""

from __future__ import annotations

from typing import Any


def pairs_list_from_forex_yaml(fx: dict[str, Any]) -> list[str]:
    p = fx.get("pairs")
    if isinstance(p, dict):
        out: list[str] = []
        for key in ("primary", "secondary"):
            block = p.get(key)
            if isinstance(block, list):
                out.extend(str(x).strip() for x in block)
        return [x for x in out if x]
    if isinstance(p, list):
        return [str(x).strip() for x in p if str(x).strip()]
    return []


def effective_session_dict(fx: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level ``session`` with ``auto_paper`` session_* overrides."""

    base = fx.get("session") if isinstance(fx.get("session"), dict) else {}
    ap = fx.get("auto_paper") if isinstance(fx.get("auto_paper"), dict) else {}
    sess = dict(base)
    if ap.get("session_timezone"):
        sess["timezone"] = str(ap["session_timezone"])
    if ap.get("session_window"):
        sess["window"] = str(ap["session_window"])
    if not sess.get("timezone"):
        sess["timezone"] = "Australia/Melbourne"
    if not sess.get("days"):
        sess["days"] = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    return sess


__all__ = ["pairs_list_from_forex_yaml", "effective_session_dict"]
