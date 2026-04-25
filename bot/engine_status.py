"""Read-only engine / lab status aggregation (no broker, no TWS on import)."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config

# Paths must match UI / execution conventions (13G).
KILL_SWITCH_RELPATH = "data/KILL_SWITCH"
STRATEGY_LAB_UI_PID_RELPATH = "data/runtime/strategy_lab_ui.pid"
MTF_AUTO_PAPER_RELPATH = "data/runtime/mtf_auto_paper_enabled"
INTRADAY_AUTO_PAPER_RELPATH = "data/runtime/intraday_auto_paper_enabled"
INTRADAY_LOOP_STATE_RELPATH = "data/runtime/intraday_auto_paper_loop_state.json"


def _sort_latest(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return paths[-1]


def _latest_under(root: Path, pattern: str) -> str | None:
    if not root.is_dir():
        return None
    cands = sorted(root.glob(pattern))
    p = _sort_latest(cands)
    return str(p) if p else None


def _probe_healthz(url: str, *, timeout: float = 2.0) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "url": url, "error": None, "body": None}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            out["status_code"] = resp.getcode()
            try:
                out["body"] = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                out["body_raw"] = raw[:500]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc!s}"
    else:
        out["ok"] = True
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_engine_status_payload(
    cfg: AppConfig,
    *,
    probe_ui: bool = False,
) -> dict[str, Any]:
    """Aggregate config + optional UI probe + latest artifact paths. No broker."""
    from .execution.intraday_paper_execution import (  # noqa: PLC0415
        INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
        is_intraday_paper_runtime_enabled,
        is_kill_switch_active,
    )
    from .strategies import default_registry  # noqa: PLC0415

    t = cfg.settings.trading
    acct = cfg.settings.account
    _def_host, _def_port = "127.0.0.1", 8765
    host = (os.environ.get("STRATEGY_LAB_HOST") or _def_host).strip()
    port_s = (os.environ.get("STRATEGY_LAB_PORT") or str(_def_port)).strip()
    try:
        port = int(port_s)
    except ValueError:
        port = _def_port
    mtf_p = Path(cfg.absolute("data/runtime/mtf_auto_paper_enabled"))
    reg = default_registry()
    meta_keys = [m.key for m in reg.list_metadata()]
    strat_cfg = str(cfg.project_root / "config" / "strategies.yaml")
    try:
        from .strategies import load_strategies_config  # noqa: PLC0415

        candidate = cfg.project_root / "config" / "strategies.yaml"
        if candidate.is_file():
            rcfg = load_strategies_config(candidate)
        else:
            rcfg = load_strategies_config(Path("config/strategies.yaml"))
        if rcfg.source_path:
            strat_cfg = str(rcfg.source_path)
    except Exception:  # noqa: BLE001
        pass
    intraday_on, intraday_off_explicit = is_intraday_paper_runtime_enabled(cfg)
    ip = t.intraday_paper
    pr = cfg.project_root
    porders = Path(cfg.absolute("data/paper_orders"))
    jlogs: list[Path] = sorted(porders.glob("*.jsonl")) if porders.is_dir() else []

    artifacts: dict[str, Any] = {
        "latest_research_json": _latest_under(Path(cfg.absolute("data/research")), "*-research-report.json"),
        "latest_research_instructions": _latest_under(
            Path(cfg.absolute("data/research")), "*-research-instructions.json"
        ),
        "latest_dynamic_watchlist": _latest_under(
            Path(cfg.absolute("data/watchlists")), "*-dynamic-watchlist.json"
        ),
        "latest_intraday_watchlist_summary": _latest_under(
            Path(cfg.absolute("data/intraday_smc")), "*-watchlist-intraday-smc-summary.json"
        ),
        "latest_mtf_watchlist_summary": _latest_under(
            Path(cfg.absolute("data/mtf_smc")), "*-watchlist-mtf-smc-summary.json"
        ),
        "latest_backtest_summary": _latest_under(
            Path(cfg.absolute("data/backtests/intraday")), "*-backtest-summary.json"
        ),
        "intraday_loop_state_path": str(Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH)))
        if Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH)).is_file()
        else None,
        "latest_paper_order_log": _latest_under(
            Path(cfg.absolute("data/paper_orders")), "*-intraday-paper-orders.jsonl"
        ),
    }
    if jlogs:
        artifacts["newest_paper_jsonl"] = str(jlogs[-1])

    payload: dict[str, Any] = {
        "ok": True,
        "paper_only": True,
        "live_trading": False,
        "project_root": str(pr),
        "trading": {
            "enabled": bool(t.enabled),
            "mtf_paper_dry_run": bool(t.mtf_paper_dry_run),
            "intraday_paper": {
                "config_enabled": bool(ip.enabled),
                "runtime_on": intraday_on,
                "runtime_explicit_off": intraday_off_explicit,
            },
        },
        "account": {
            "mode": str(acct.mode),
            "block_live_trading": bool(acct.block_live_trading),
        },
        "kill_switch_active": is_kill_switch_active(cfg),
        "runtime_paths": {
            "kill_switch": str(Path(cfg.absolute(KILL_SWITCH_RELPATH))),
            "mtf_auto_paper": str(mtf_p),
            "intraday_auto_paper": str(Path(cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH))),
        },
        "strategies": {
            "registry_keys": meta_keys,
            "strategies_yaml": strat_cfg,
        },
        "ui": {
            "default_base_url": f"http://{host}:{port}",
            "healthz": f"http://{host}:{port}/healthz",
            "start_command": "python -m bot_ui",
        },
        "artifacts": artifacts,
    }

    ui_proc: dict[str, Any] = {
        "pid_path": str(Path(cfg.absolute(STRATEGY_LAB_UI_PID_RELPATH))),
        "running": False,
        "pid": None,
        "healthz": None,
    }
    pp = Path(cfg.absolute(STRATEGY_LAB_UI_PID_RELPATH))
    if pp.is_file():
        try:
            raw = pp.read_text(encoding="utf-8").strip()
            pid = int(raw.splitlines()[0].strip() if raw else 0) if raw else 0
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            ui_proc["pid"] = pid
            ui_proc["running"] = True
            if probe_ui:
                base = f"http://{host}:{port}"
                ui_proc["healthz"] = _probe_healthz(f"{base}/healthz")
    else:
        if probe_ui:
            base = f"http://{host}:{port}"
            ui_proc["healthz"] = _probe_healthz(f"{base}/healthz")
    payload["ui_process"] = ui_proc
    return payload


def run_engine_status_cli(
    *, as_json: bool, probe_ui: bool
) -> int:
    """Entry for Typer: print and return exit code 0 on success."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.console import Console  # noqa: PLC0415

    console = Console()
    cfg = load_config()
    payload = build_engine_status_payload(cfg, probe_ui=probe_ui)
    if as_json:
        import sys  # noqa: PLC0415

        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="engine-status",
                style="cyan",
            )
        )
    return 0


def check_ibkr_paper_port(host: str, port: int, *, timeout: float = 1.0) -> tuple[bool, str]:
    """TCP connect; read-only, may fail if TWS is down. Returns (ok, message)."""
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        sock.close()
    except OSError as exc:
        return False, f"connect {host}:{port} failed: {exc!s}"
    return True, f"tcp {host}:{port} reachable"
