"""TWS/API health probes + Telegram alerts (read-only). No orders."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zoneinfo import ZoneInfo

from .full_auto_paper_readiness import tws_port_listening

if TYPE_CHECKING:
    from .config import AppConfig
    from .journal import Journal

logger = logging.getLogger(__name__)

TWS_HEALTH_ALERT_STATE_RELPATH = "data/runtime/tws_health_alert_state.json"

ALERT_TW_PORT_DOWN = "tws_port_down"
ALERT_IBKR_CONNECT_FAILED = "ibkr_connect_failed"
ALERT_TW_LOGGED_OFF = "tws_logged_off_or_session_unavailable"
ALERT_NOT_PAPER = "paper_account_not_detected"
ALERT_RECON_IBKR_UNAVAILABLE = "reconciliation_ibkr_unavailable"
ALERT_TW_RECOVERED = "tws_recovered"


@dataclass
class TWSHealthStatus:
    checked_at_utc: str = ""
    tws_port_listening: bool | None = None
    ibkr_connected: bool | None = None
    paper_account: bool | None = None
    account_mode: str | None = None
    status: str = "unknown"
    alert_code: str | None = None
    """Canonical code when unhealthy; recovery uses ``tws_recovered``."""

    reason: str = ""
    raw_error_safe: str = ""
    """Public-safe excerpt; never tokens or passwords."""

    reconcile_probe_failed: bool | None = None

    extras: dict[str, Any] = field(default_factory=dict)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _safe_err(exc: BaseException | None, msg: str | None = None, limit: int = 420) -> str:
    blob = ""
    if exc is not None:
        blob = f"[{type(exc).__name__}] {exc!s}"
    elif msg:
        blob = str(msg)
    blob = blob.replace("`", "").strip()
    # Strip anything that looks like a token/long hex
    blob = re.sub(r"\b\d{10,}\b", "***", blob)
    if len(blob) > limit:
        blob = blob[:limit] + "…"
    return blob


def _ny_display(ts_utc: str | None) -> str:
    """Human NY time label for Telegram bodies."""
    if not ts_utc:
        ts_utc = _utc_iso()
    try:
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ny = dt.astimezone(ZoneInfo("America/New_York"))
        return ny.strftime("%Y-%m-%d %H:%M NY")
    except (TypeError, ValueError):
        return ts_utc


def _summarize_fatal(msg: str) -> str:
    m = (msg or "").strip()
    low = m.lower()
    if "connection refused" in low or "errno 61" in low or "[errno 61]" in low:
        return "connection refused"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "network" in low and ("unreachable" in low or "down" in low):
        return "network unreachable"
    return _safe_err(RuntimeError(msg), msg=msg)


def alert_state_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / TWS_HEALTH_ALERT_STATE_RELPATH


def load_alert_state(project_root: Path | str) -> dict[str, Any]:
    p = alert_state_path(project_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_alert_state(project_root: Path | str, data: dict[str, Any]) -> None:
    root = Path(project_root).resolve()
    p = alert_state_path(root)
    cur = load_alert_state(root)
    cur.update(data)
    cur["updated_at_utc"] = _utc_iso()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cur, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def fix_save_alert_state(project_root: Path | str, data: dict[str, Any]) -> None:
    """Merge into existing state."""
    root = Path(project_root).resolve()
    cur = load_alert_state(root)
    cur.update(data)
    cur["updated_at_utc"] = _utc_iso()
    p = alert_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cur, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def check_tws_health_for_alerts(
    cfg: "AppConfig",
    journal: Journal | None = None,
) -> TWSHealthStatus:
    """Read-only probes: TCP → optional IBKR read-only connect (+ optional reconcile)."""

    checked = _utc_iso()
    host = str(getattr(cfg.ibkr, "host", "127.0.0.1") or "127.0.0.1")
    try:
        port = int(getattr(cfg.ibkr, "port", 7497) or 7497)
    except (TypeError, ValueError):
        port = 7497

    want_paper_cfg = str(getattr(cfg.settings.account, "mode", "paper")).lower() == "paper"

    st = TWSHealthStatus(checked_at_utc=checked, account_mode=getattr(cfg.ibkr, "account_mode", None))
    plist = tws_port_listening(host, port)
    st.tws_port_listening = bool(plist)

    if not plist:
        st.status = "port_down"
        st.alert_code = ALERT_TW_PORT_DOWN
        st.reason = "TWS/IB Gateway API port not accepting TCP connections"
        st.raw_error_safe = f"{host}:{port} not reachable (socket connect failed)"
        st.ibkr_connected = False
        st.paper_account = None
        return st

    from .ibkr_connection import connect_readonly_roster_retry  # noqa: PLC0415
    from .ibkr_client import IBKRClientError  # noqa: PLC0415

    outcome = connect_readonly_roster_retry(cfg, "broker_readonly", ib_connect_timeout=12.0)
    meta_lines = "\n".join(outcome.log_lines or [])[-800:]

    if outcome.live_blocked is not None:
        st.status = "wrong_account"
        st.alert_code = ALERT_NOT_PAPER
        st.ibkr_connected = False
        st.paper_account = False
        st.reason = "Live/paper guard blocked read-only API (possible non-paper endpoint)"
        st.raw_error_safe = _safe_err(outcome.live_blocked)
        return st

    if outcome.client is None:
        msg = outcome.fatal_message or "broker connection unavailable"
        st.status = "connect_failed"
        st.alert_code = ALERT_IBKR_CONNECT_FAILED
        st.ibkr_connected = False
        st.reason = _summarize_fatal(msg)
        st.raw_error_safe = _safe_err(RuntimeError(str(msg)), msg=meta_lines or str(msg))
        return st

    client = outcome.client
    try:
        sess = client.session_status_snapshot()
        if not isinstance(sess, dict):
            sess = {}
        st.extras["session_keys"] = list(sess.keys())
        reconnect_ok = bool(sess.get("connected"))
        am_raw = sess.get("account_mode")
        account_mode_sess = str(am_raw).lower().strip() if am_raw else None
        st.ibkr_connected = reconnect_ok
        st.extras["session_connected"] = reconnect_ok

        cfg_mode = str(getattr(cfg.ibkr, "account_mode", "") or "").lower()
        sess_mode_l = account_mode_sess or ""
        pts = bool(sess.get("paper_trading_supported"))
        looks_paper = (
            sess_mode_l in {"paper", "demo"}
            or pts
            or (
                sess_mode_l in {"", "unknown"}
                and want_paper_cfg
                and reconnect_ok
            )
        )
        if reconnect_ok:
            st.paper_account = bool(looks_paper)

        if not reconnect_ok:
            client.disconnect()
            st.status = "logged_off"
            st.alert_code = ALERT_TW_LOGGED_OFF
            st.reason = "IBKR reports session not connected (TWS logged off or API disconnected)"
            st.raw_error_safe = "session.connected is false — log into TWS / enable API socket"
            return st

        if want_paper_cfg and reconnect_ok and not looks_paper and sess_mode_l:
            sess_is_live = sess_mode_l in {"live"}
            sess_is_explicit_non_paper = (
                sess_mode_l not in {"paper", "demo"}
                and not pts
                and sess_mode_l not in {"", "unknown"}
            )
            if sess_is_live or sess_is_explicit_non_paper:
                client.disconnect()
                st.status = "wrong_account"
                st.alert_code = ALERT_NOT_PAPER
                st.paper_account = False
                st.reason = "Paper account mode not reflected by IBKR session"
                st.raw_error_safe = (
                    f"session.account_mode={account_mode_sess!r}" if account_mode_sess else ""
                )
                return st

        reconcile_failed = False
        if journal is not None and cfg.settings.trading.intraday_paper.require_reconciliation_pass:
            from .broker import Broker  # noqa: PLC0415
            from .reconciliation import reconcile  # noqa: PLC0415

            try:
                broker = Broker(cfg, client, journal)
                rep = reconcile(broker, journal)
                if rep is None:
                    reconcile_failed = True
                else:
                    st.extras["reconcile_passed"] = bool(rep.passed)
                    if not rep.passed:
                        reconcile_failed = True
            except (IBKRClientError, OSError, RuntimeError, TypeError, ValueError) as exc:
                reconcile_failed = True
                st.extras["reconcile_exc"] = type(exc).__name__

        client.disconnect()

        if reconcile_failed:
            st.alert_code = ALERT_RECON_IBKR_UNAVAILABLE
            st.status = "reconcile_unavailable"
            st.ibkr_connected = True
            st.reason = "Reconciliation/read-only broker view failed despite partial connection"
            st.raw_error_safe = "reconcile unavailable — check journal / TWS log"
            st.reconcile_probe_failed = True
            return st

        st.account_mode = account_mode_sess or cfg_mode or None

        st.status = "healthy"
        st.alert_code = None
        st.reason = ""
        st.raw_error_safe = ""
        return st

    except (IBKRClientError, OSError, TimeoutError, RuntimeError, TypeError) as exc:
        try:
            client.disconnect()
        except Exception:
            pass
        st.ibkr_connected = False
        st.alert_code = ALERT_IBKR_CONNECT_FAILED
        st.status = "connect_failed"
        st.reason = _summarize_fatal(repr(exc))
        st.raw_error_safe = _safe_err(exc)
        return st


def format_unhealthy_telegram_zh(
    *,
    status_obj: TWSHealthStatus,
    source: str,
) -> str:
    code_map = {
        ALERT_TW_PORT_DOWN: ("TWS/API 不可用", "API 监听端口无法接受连接"),
        ALERT_IBKR_CONNECT_FAILED: ("TWS/API 不可用", status_obj.reason or "IBKR 连接失败"),
        ALERT_TW_LOGGED_OFF: ("TWS 未登录或未就绪", status_obj.reason or "会话不可用"),
        ALERT_NOT_PAPER: ("非纸面或未识别为 Paper", status_obj.reason or "账户模式异常"),
        ALERT_RECON_IBKR_UNAVAILABLE: ("对账无法完成", status_obj.reason or "IBKR 不可用"),
        "unknown": ("TWS/API 未知问题", status_obj.reason),
    }
    title_cn, subtitle = code_map.get(
        status_obj.alert_code or "",
        ("TWS/API 异常", status_obj.reason),
    )
    ny = _ny_display(status_obj.checked_at_utc)
    rs = status_obj.raw_error_safe or subtitle
    return (
        "<b>【TWS 警报】</b>\n"
        f"<b>状态:</b> {title_cn}\n"
        f"<b>代码:</b> <code>{status_obj.alert_code or ''}</code>\n"
        f"<b>原因:</b> {subtitle}\n"
        f"<b>详情:</b> <pre>{escape_html(rs)}</pre>\n"
        "<b>影响:</b> 自动纸面引擎无法下单或对账\n"
        "<b>动作:</b> 请打开/登录 TWS Paper，并确认 API 7497 已启用 · Mac 保持唤醒\n"
        f"<b>时间:</b> {ny}\n"
        f"<b>来源:</b> <code>{escape_html(source)}</code>\n"
        "<i>只读告警 · Paper only · 未下单</i>"
    )


def format_recovery_telegram_zh(*, checked_at_utc: str, paper_account: bool | None, source: str) -> str:
    ny = _ny_display(checked_at_utc)
    pap = paper_account if paper_account is not None else True
    return (
        "<b>【TWS 恢复】</b>\n"
        "<b>状态:</b> TWS/API 已恢复 · healthy\n"
        f"<b>纸面账户:</b> {pap}\n"
        f"<b>时间:</b> {ny}\n"
        f"<b>来源:</b> <code>{escape_html(source)}</code>\n"
        "<i>只读告警 · Paper only · 未下单</i>"
    )


def escape_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def maybe_send_tws_health_alert(
    cfg: "AppConfig",
    journal: Journal | None,
    status: TWSHealthStatus,
    *,
    source: str,
    send_telegram: bool = True,
) -> dict[str, Any]:
    """Update state JSON; optionally send Telegram (throttled, recovery once)."""

    root = Path(cfg.project_root).resolve()
    tac = getattr(cfg.settings.trading, "tws_health_alerts", None)
    enabled_default = getattr(tac, "enabled", True) if tac is not None else True
    min_m = getattr(tac, "min_interval_minutes", 15) if tac is not None else 15
    send_recovery = getattr(tac, "send_recovery", True) if tac is not None else True

    if not enabled_default:
        fix_save_alert_state(root, {"alerting_enabled_false": True})
        return {"sent": False, "reason": "tws_health_alerts disabled in config"}

    if not getattr(cfg, "telegram", None) or not cfg.telegram.is_configured:
        return {"sent": False, "reason": "telegram_not_configured"}

    if not send_telegram:
        fix_save_alert_state(root, {"last_status": status.status, "alert_code_seen": status.alert_code})
        return {"sent": False, "reason": "send disabled for this invocation"}

    st_prev = load_alert_state(root)
    last_code = str(st_prev.get("last_sent_alert_code") or "")
    last_ts = str(st_prev.get("last_sent_alert_at_utc") or "")
    was_alerting = bool(st_prev.get("was_alerting", False))

    healthy = status.status == "healthy"
    unhealthy = not healthy and status.alert_code and status.alert_code != ALERT_TW_RECOVERED

    info: dict[str, Any] = {"sent": False, "recovery_sent": False, "skipped": False}

    from .notifications.telegram import send_telegram_message  # noqa: PLC0415

    now = datetime.now(timezone.utc)

    # Recovery branch
    if healthy and was_alerting and send_recovery:
        fix_save_alert_state(
            root,
            {
                "was_alerting": False,
                "last_recovery_at_utc": _utc_iso(),
                "last_healthy_checked_at": status.checked_at_utc,
            },
        )
        info["recovery_sent"] = False
        try:
            body = format_recovery_telegram_zh(
                checked_at_utc=status.checked_at_utc,
                paper_account=status.paper_account,
                source=source,
            )
            send_telegram_message(body, cfg=cfg, journal=journal)
            info["recovery_sent"] = True
        except Exception as exc:
            logger.warning("tws recovery telegram failed (non-fatal): %s", exc)
            info["telegram_error"] = type(exc).__name__
        fix_save_alert_state(
            root,
            {
                "last_recovery_sent_at_utc": _utc_iso() if info["recovery_sent"] else st_prev.get("last_recovery_sent_at_utc"),
            },
        )
        return info

    if unhealthy and status.alert_code:
        dup = last_code == status.alert_code
        throttle_ok = True
        if dup and last_ts:
            try:
                prev = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if prev.tzinfo is None:
                    prev = prev.replace(tzinfo=timezone.utc)
                delta_min = (now - prev).total_seconds() / 60.0
                throttle_ok = delta_min >= float(min_m)
            except (TypeError, ValueError):
                throttle_ok = True

        if not throttle_ok:
            fix_save_alert_state(
                root,
                {
                    "last_check_at_utc": status.checked_at_utc,
                    "last_skip_throttle": True,
                    "was_alerting": True,
                },
            )
            info["skipped"] = True
            info["reason"] = "throttled"
            return info

        try:
            body = format_unhealthy_telegram_zh(status_obj=status, source=source)
            send_telegram_message(body, cfg=cfg, journal=journal)
            info["sent"] = True
            fix_save_alert_state(
                root,
                {
                    "last_sent_alert_code": status.alert_code,
                    "last_sent_alert_at_utc": _utc_iso(),
                    "last_alert_reason_safe": status.reason[:300],
                    "was_alerting": True,
                    "last_unhealthy_checked_at": status.checked_at_utc,
                },
            )
        except Exception as exc:
            logger.warning("tws health telegram failed (non-fatal): %s", exc)
            info["telegram_error"] = type(exc).__name__
            fix_save_alert_state(root, {"last_send_failure": type(exc).__name__})
        return info

    if healthy:
        fix_save_alert_state(
            root,
            {
                "was_alerting": False,
                "last_healthy_checked_at": status.checked_at_utc,
                "last_status": status.status,
            },
        )
    else:
        fix_save_alert_state(root, {"last_status": status.status, "partial": True})

    return info


def health_status_from_broker_snapshot(snap_dict: dict[str, Any]) -> TWSHealthStatus:
    """Map ``BrokerSnapshot.to_dict()`` to :class:`TWSHealthStatus` (no extra IBKR connects)."""

    st = TWSHealthStatus(checked_at_utc=str(snap_dict.get("checked_at_utc") or _utc_iso()))
    twl = snap_dict.get("tws_listening")
    st.tws_port_listening = None if twl is None else bool(twl)
    st.ibkr_connected = snap_dict.get("ibkr_connected")
    st.account_mode = str(snap_dict.get("account_mode") or "") or None
    st.paper_account = snap_dict.get("paper_account")
    raw_err = str(snap_dict.get("error_summary") or "")[:500]
    st.raw_error_safe = _safe_err(RuntimeError(raw_err), msg=raw_err) if raw_err else ""

    status = str(snap_dict.get("status") or "").lower()

    rl = raw_err.lower()

    lt_blocked = (
        "livetradingblocked" in "".join(raw_err.lower().split())
        or ("live trading" in rl and "block" in rl)
        or ("block" in rl and "paper" not in rl and "live" in rl and status == "error")
    )

    if lt_blocked:
        st.alert_code = ALERT_NOT_PAPER
        st.status = "wrong_account"
        st.paper_account = False
        st.reason = raw_err[:280] if raw_err else "live trading guard triggered"
        return st

    if status == "ok":
        conn = snap_dict.get("ibkr_connected")
        if conn is False:
            st.status = "logged_off"
            st.alert_code = ALERT_TW_LOGGED_OFF
            st.reason = "Broker snapshot ok but ibkr_connected=false"
            return st
        st.status = "healthy"
        return st

    if st.tws_port_listening is False:
        st.alert_code = ALERT_TW_PORT_DOWN
        st.status = "port_down"
        st.reason = "snapshot: TCP/TWS endpoint unreachable"
        st.ibkr_connected = False
        return st

    if "live" in raw_err.lower() or "blocked" in raw_err.lower():
        st.alert_code = ALERT_NOT_PAPER
        st.status = "wrong_account"
        st.paper_account = False
        st.reason = raw_err[:200] or "possible non-paper endpoint"
        return st

    st.alert_code = ALERT_IBKR_CONNECT_FAILED
    st.status = "connect_failed"
    st.reason = raw_err[:200] if raw_err else "broker snapshot unavailable"
    st.ibkr_connected = snap_dict.get("ibkr_connected")
    return st


def status_as_dict(status: TWSHealthStatus) -> dict[str, Any]:
    """JSON-serializable view for CLI/dashboard."""

    d = {
        "checked_at_utc": status.checked_at_utc,
        "tws_port_listening": status.tws_port_listening,
        "ibkr_connected": status.ibkr_connected,
        "paper_account": status.paper_account,
        "account_mode": status.account_mode,
        "status": status.status,
        "alert_code": status.alert_code,
        "reason": status.reason,
        "raw_error_safe": status.raw_error_safe,
        "reconcile_probe_failed": status.reconcile_probe_failed,
        "extras": dict(status.extras),
    }
    return d


def ui_alert_overlay(project_root: Path | str, status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Non-secret summary for Dashboard/Paper pages."""

    st = load_alert_state(Path(project_root).resolve())
    last_code = str(st.get("last_sent_alert_code") or "")
    recovery = str(st.get("last_recovery_sent_at_utc") or st.get("last_recovery_at_utc") or "")
    alerting = bool(st.get("was_alerting", False))
    return {
        "healthy_hint": not alerting,
        "last_alert_code": last_code or None,
        "last_alert_sent_at_utc": st.get("last_sent_alert_at_utc"),
        "last_recovery_sent_at_utc": recovery or None,
        "last_reason_safe": (st.get("last_alert_reason_safe") or "")[:280],
        "updated_at_utc": st.get("updated_at_utc"),
        "instant_check": status,
    }


__all__ = [
    "ALERT_TW_PORT_DOWN",
    "ALERT_IBKR_CONNECT_FAILED",
    "ALERT_TW_LOGGED_OFF",
    "ALERT_NOT_PAPER",
    "ALERT_RECON_IBKR_UNAVAILABLE",
    "ALERT_TW_RECOVERED",
    "TWSHealthStatus",
    "TWS_HEALTH_ALERT_STATE_RELPATH",
    "check_tws_health_for_alerts",
    "maybe_send_tws_health_alert",
    "load_alert_state",
    "save_alert_state",
    "fix_save_alert_state",
    "ui_alert_overlay",
    "status_as_dict",
    "format_recovery_telegram_zh",
    "health_status_from_broker_snapshot",
]

