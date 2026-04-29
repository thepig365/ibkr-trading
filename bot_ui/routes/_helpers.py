"""Shared route helpers."""

from __future__ import annotations


import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

from ..i18n import append_lang_to_path, get_locale, lang_switch_href, t as translate
from ..services.command_queue import CommandResult
from ..services.state_store import StateStore


def _project_root_short(p: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)


def base_context(
    request: Request,
    *,
    active: str,
    flash: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Common context every page template needs."""
    state: StateStore = request.app.state.state_store
    safety = state.safety_view()
    root = request.app.state.project_root
    locale = get_locale(request)

    def t(key: str, **kwargs: str | float) -> str:  # noqa: ANN401
        return translate(key, locale, **kwargs)

    def ret(path: str) -> str:
        return append_lang_to_path(path, locale)

    def lang_href(target: str) -> str:
        return lang_switch_href(request, target)

    return {
        "request": request,
        "active": active,
        "backend": request.app.state.backend,
        "safety": safety,
        "ui_host": request.app.state.ui_host,
        "ui_port": request.app.state.ui_port,
        "project_root": str(root),
        "project_root_short": _project_root_short(root),
        "flash": flash,
        "doc_manual": "docs/strategy-lab-user-manual.md",
        "doc_checklist": "docs/daily-operation-checklist.md",
        "doc_troubleshooting": "docs/troubleshooting.md",
        "locale": locale,
        "html_lang": "zh" if locale == "zh" else "en",
        "t": t,
        "ret": ret,
        "lang_href": lang_href,
    }


_CHART_FAMILY = frozenset(
    {"complete-trade-charts", "tradervue-complete-charts", "complete-journal-charts"}
)


def _parse_json_tail(stdout: str) -> dict[str, Any] | None:
    if not stdout or not stdout.strip():
        return None
    blob = stdout.strip()
    i = blob.rfind("{")
    if i < 0:
        return None
    try:
        return json.loads(blob[i:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _parse_iso_age_seconds(timestamp: str | None) -> float | None:
    if not timestamp or not isinstance(timestamp, str):
        return None
    try:
        fixed = timestamp.replace("Z", "+00:00") if timestamp.endswith("Z") else timestamp
        ts = datetime.fromisoformat(fixed)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(tz=timezone.utc) - ts).total_seconds())
    except (ValueError, TypeError, OSError):
        return None


def dashboard_flash_from_recent_command(
    result: CommandResult | None,
    *,
    t: Any,
    max_age_seconds: float | None = 300.0,
) -> dict[str, str] | None:
    """One-line UX banner after POST → redirect: show outcome without scrolling.

    Avoids flashing stale commands from hours ago unless they finished within ``max_age_seconds``.
    """
    if result is None:
        return None
    raw_ts = getattr(result, "finished_utc", None) or getattr(result, "started_utc", None)
    age = _parse_iso_age_seconds(raw_ts) if isinstance(raw_ts, str) else None
    if max_age_seconds is not None:
        if age is None:
            return None
        if age > float(max_age_seconds):
            return None

    base = flash_from_result(result)
    if base is None:
        return None
    cmd = (result.request.command or "").strip()

    if not result.accepted:
        msg = (
            base["message"].replace(
                "Command rejected:", t("dashboard.flash_cmd_rejected_label") + ":"
            )
            if getattr(t, "__call__", None)
            else base["message"]
        )
        return {"kind": "fail", "message": msg}

    if cmd in _CHART_FAMILY and "--json" in result.request.args:
        summary = _parse_json_tail(result.stdout or "")
        if isinstance(summary, dict) and summary:
            def pick(k: str) -> str:
                v = summary.get(k)
                return str(v) if v is not None else "—"

            gen_raw = summary.get("generated_count")
            wc_raw = summary.get("would_generate_count")
            extra = ""
            try:
                if (
                    wc_raw is not None
                    and int(wc_raw) == 0
                    and gen_raw is not None
                    and int(gen_raw) == 0
                ):
                    extra = " " + t("dashboard.flash_trade_charts_none_hint")
            except (TypeError, ValueError):
                pass
            return {
                "kind": "ok" if result.exit_code == 0 else "fail",
                "message": t(
                    "dashboard.flash_trade_charts_detail",
                    base=(base["message"]).rstrip("."),
                    generated=pick("generated_count"),
                    missing=pick("missing_candles_count"),
                    errors=pick("error_count"),
                    eligible=pick("available_count"),
                    no_exit=pick("no_exit_count"),
                    skipped=pick("skipped_status_count"),
                )
                + extra,
            }

    # broker snapshot: echo concise status from stdout JSON (--json prints envelope)
    if cmd in {"broker-snapshot-refresh", "broker-refresh"}:
        sj = _parse_json_tail(result.stdout or "")
        if isinstance(sj, dict) and sj.get("status"):
            msg = (
                base["message"]
                + " "
                + t(
                    "dashboard.flash_broker_snapshot_detail",
                    status=str(sj.get("status") or ""),
                    positions=str(sj["positions_count"])
                    if sj.get("positions_count") is not None
                    else "—",
                    orders=str(sj["open_orders_count"])
                    if sj.get("open_orders_count") is not None
                    else "—",
                )
            )
            return {"kind": "ok" if result.exit_code == 0 else "fail", "message": msg}

    if cmd == "reconcile-fills" and "--json" in result.request.args:
        sj = _parse_json_tail(result.stdout or "")
        if isinstance(sj, dict) and (sj.get("date") or sj.get("reconciled_at_utc")):

            def pick_num(k: str) -> str:
                v = sj.get(k)
                return str(v) if v is not None else "—"

            return {
                "kind": "ok" if result.exit_code == 0 else "fail",
                "message": t(
                    "dashboard.flash_reconcile_fills",
                    base=(base["message"]).rstrip("."),
                    fills=pick_num("fills_count"),
                    closed=pick_num("closed_count"),
                    fo=pick_num("filled_open_count"),
                    snf=pick_num("submitted_not_filled_count"),
                ),
            }

    return base


def flash_from_result(result: CommandResult | None) -> dict[str, str] | None:
    if result is None:
        return None
    if not result.accepted:
        return {"kind": "fail", "message": f"Command rejected: {result.rejected_reason}"}
    if result.exit_code == 0:
        return {
            "kind": "ok",
            "message": (
                f"Ran {result.request.display()} OK in "
                f"{result.duration_seconds or 0:.1f}s."
            ),
        }
    return {
        "kind": "fail",
        "message": (
            f"Command {result.request.display()} exited "
            f"{result.exit_code} (see Recent commands)."
        ),
    }


def parse_bool_param(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
