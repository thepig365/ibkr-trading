"""UI context bits for Forex auto paper — file reads only (no TWS)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import AppConfig, load_config
from bot.execution.intraday_paper_execution import is_kill_switch_active

from .auto_paper_readiness import build_forex_auto_paper_readiness
from .config import load_forex_ict_config, melbourne_session_open
from .runtime_auto import enabled_path as forex_runtime_enabled_path
from .yaml_utils import effective_session_dict, pairs_list_from_forex_yaml

# Keep UI import lightweight: importing auto_paper_supervisor pulls IBKRClient.
LOOP_STATE_UI_RELPATH = "data/runtime/forex_auto_paper_loop_state.json"


def _launchd_installed_hint() -> bool:
    plist = Path.home() / "Library/LaunchAgents/com.strategy-lab.forex-auto-paper.plist"
    return plist.is_file()


def _runtime_enabled_plain(root: Path) -> bool:
    p = forex_runtime_enabled_path(root)
    if not p.is_file():
        return False
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return isinstance(j, dict) and bool(j.get("enabled"))


def build_forex_auto_paper_dashboard(
    project_root: Path | str, *, cfg: AppConfig | None = None
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    cfg = cfg or load_config(project_root=root)
    fx = load_forex_ict_config(root)

    fx_eff = {**fx, "session": effective_session_dict(fx)}
    lp = root / LOOP_STATE_UI_RELPATH

    loop: dict[str, Any] = {}
    if lp.is_file():
        try:
            loop = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            loop = {}

    now = datetime.now(timezone.utc)
    in_sess, lh = melbourne_session_open(fx_eff, now_tz=now)

    rk = fx.get("risk") if isinstance(fx.get("risk"), dict) else {}
    ap_cfg = fx.get("auto_paper") if isinstance(fx.get("auto_paper"), dict) else {}
    exec_b = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}

    readiness = build_forex_auto_paper_readiness(root, cfg, probe_ibkr=False)
    rt_en = _runtime_enabled_plain(root)

    mode = "off"
    if rt_en:
        if bool(exec_b.get("submit_to_broker")) and bool(ap_cfg.get("enabled")):
            mode = "paper-active"
        elif bool(ap_cfg.get("enabled")) or bool(exec_b.get("submit_to_broker")):
            mode = "dry-run"
        else:
            mode = "runtime-on-no-yaml-submit"

    pairs = pairs_list_from_forex_yaml(fx)

    return {
        "title_en": "Forex ICT 1M Auto Paper",
        "title_zh": "外汇 ICT 1分钟自动纸面交易",
        "legal_en": (
            "Paper only. Uses IBKR TWS paper account. Daily notional cap USD 100,000. "
            "No market orders."
        ),
        "legal_zh": (
            "仅纸面账户。使用 IBKR TWS paper。每日名义上限 USD 100,000。禁止市价单。"
        ),
        "mode": mode,
        "melbourne_session_open": in_sess,
        "melbourne_local_time": lh,
        "session_window": str(ap_cfg.get("session_window") or "09:00-17:00"),
        "daily_cap_usd": float(
            ap_cfg.get("max_daily_notional_usd") or rk.get("max_daily_notional_usd") or 100_000
        ),
        "daily_used_usd": float(loop.get("daily_notional_used_usd") or 0),
        "daily_remaining_usd": float(loop.get("daily_notional_remaining_usd") or 0),
        "pairs": pairs,
        "readiness": readiness,
        "loop_state": loop,
        "kill_switch_active": is_kill_switch_active(cfg),
        "launchd_forex_installed": _launchd_installed_hint(),
        "runtime_enabled": rt_en,
        "yaml_auto_paper_enabled": bool(ap_cfg.get("enabled")),
        "yaml_submit_to_broker": bool(exec_b.get("submit_to_broker")),
        "strategy_id": fx.get("strategy_id"),
    }


__all__ = ["build_forex_auto_paper_dashboard"]
