"""MTF 5m trigger watch: near-alignment polling, alert-only (Prompt 10F).

No orders, no broker, no config mutation. `research_only` / `execution_allowed`
in outputs are fixed for auditability.
"""

from __future__ import annotations

import json
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .journal import Journal
from .mtf_diagnostic import _BLOCK_PRIORITY, load_mtf_json
from .mtf_smc_engine import MtfCandleBundle, run_mtf_smc
from .notifications import send_telegram_message

# Watch selection: block these even if they appeared in a stale list
_TRIGGER_WATCH_EXCLUDE: frozenset[str] = frozenset(
    {
        "RISK",
        "DAILY_BIAS",
        "NEWS_OR_REGIME",
        "DATA_MISSING",
        "CONFLICTED",
        "NONE",
    }
)

RUNTIME_STATE_FILENAME = "runtime_trigger_state.json"


def find_latest_diagnostic_report_path(mtf_dir: Path) -> tuple[str, Path] | None:
    """Return (date, path) for the newest ``*-mtf-diagnostic-report.json``."""
    paths = list(mtf_dir.glob("*-mtf-diagnostic-report.json"))
    if not paths:
        return None
    latest = max(paths, key=lambda p: p.stat().st_mtime)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-mtf-diagnostic-report\.json$", latest.name)
    if not m:
        return None
    return m.group(1), latest


def _sort_watch_key(r: dict[str, Any]) -> tuple[int, int, str]:
    bl = str(r.get("blocking_layer") or "")
    pr = _BLOCK_PRIORITY.get(bl, 99)
    sc = -int(r.get("mtf_alignment_score") or 0)
    sym = str(r.get("symbol") or "").upper()
    return (pr, sc, sym)


def select_trigger_watch_candidates(
    near: list[dict[str, Any]],
    *,
    include_premium: bool = False,
    top: int = 5,
    symbol: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split near-alignment rows into (active_poll, secondary_skipped_premium).

    Active when ``blocking_layer == FIVE_MIN_TRIGGER`` *or*
    ``alignment_category == SETUP_READY_WAITING_TRIGGER``; excluded layers
    removed; ``PREMIUM_DISCOUNT`` only if ``include_premium``.
    """
    sym_u = symbol.upper().strip() if symbol else None
    secondary: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for r in near:
        raw = dict(r)
        s = str(raw.get("symbol", "")).upper()
        if sym_u and s != sym_u:
            continue
        bl = str(raw.get("blocking_layer") or "")
        cat = str(raw.get("alignment_category") or "")
        if bl in _TRIGGER_WATCH_EXCLUDE or not bl:
            continue
        is_five = bl == "FIVE_MIN_TRIGGER"
        is_setup_ready = cat == "SETUP_READY_WAITING_TRIGGER"
        if not (is_five or is_setup_ready):
            continue
        if bl == "PREMIUM_DISCOUNT" and not include_premium:
            secondary.append(raw)
            continue
        active.append(raw)
    active.sort(key=_sort_watch_key)
    return active[: max(0, int(top))], secondary


# --------------------------------------------------------------------------- #
# Runtime state
# --------------------------------------------------------------------------- #


@dataclass
class SymbolRuntimeState:
    last_trigger_state: str = "unknown"
    last_alignment_category: str = "BLOCKED"
    last_score: int = 0
    last_eligible: bool = False
    last_checked_at: str = ""
    confirmed_alert_sent_for_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_trigger_state": self.last_trigger_state,
            "last_alignment_category": self.last_alignment_category,
            "last_score": self.last_score,
            "last_eligible": self.last_eligible,
            "last_checked_at": self.last_checked_at,
            "confirmed_alert_sent_for_date": self.confirmed_alert_sent_for_date,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SymbolRuntimeState:
        return cls(
            last_trigger_state=str(d.get("last_trigger_state", "unknown")),
            last_alignment_category=str(d.get("last_alignment_category", "BLOCKED")),
            last_score=int(d.get("last_score", 0) or 0),
            last_eligible=bool(d.get("last_eligible", False)),
            last_checked_at=str(d.get("last_checked_at", "")),
            confirmed_alert_sent_for_date=str(
                d.get("confirmed_alert_sent_for_date", "") or ""
            ),
        )


@dataclass
class RuntimeTriggerStore:
    symbols: dict[str, SymbolRuntimeState] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbols": {k: v.to_dict() for k, v in self.items()},
        }

    def items(self) -> list[tuple[str, SymbolRuntimeState]]:
        return sorted(self.symbols.items(), key=lambda kv: kv[0])

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RuntimeTriggerStore:
        sy: dict[str, SymbolRuntimeState] = {}
        for k, v in (d.get("symbols") or {}).items():
            if isinstance(v, dict):
                sy[str(k).upper()] = SymbolRuntimeState.from_dict(v)
        return cls(symbols=sy, version=int(d.get("version", 1)))


def load_runtime_trigger_state(path: Path) -> RuntimeTriggerStore:
    if not path.exists():
        return RuntimeTriggerStore()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return RuntimeTriggerStore()
        return RuntimeTriggerStore.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return RuntimeTriggerStore()


def save_runtime_trigger_state(path: Path, store: RuntimeTriggerStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(store.to_dict(), f, indent=2, ensure_ascii=False, default=str)


def _meaningful_state_change(
    prev: SymbolRuntimeState,
    *,
    cur_trigger: str,
    cur_cat: str,
    cur_elig: bool,
    cur_score: int,
) -> bool:
    if cur_trigger != prev.last_trigger_state:
        return True
    if cur_cat != prev.last_alignment_category:
        return True
    if bool(cur_elig) != bool(prev.last_eligible):
        return True
    if abs(int(cur_score) - int(prev.last_score)) >= 10:
        return True
    return False


def _previous_trigger_from_saved_mtf(
    mtf_dir: Path, date: str, symbol: str
) -> str:
    p = mtf_dir / f"{date}-{symbol.upper()}-mtf-smc.json"
    if not p.exists():
        return "unknown"
    try:
        m = load_mtf_json(p)
        t5 = (m.get("timeframes") or {}).get("5min") or {}
        return str(t5.get("trigger_state", "unknown"))
    except (OSError, json.JSONDecodeError, TypeError):
        return "unknown"


def _normalise_trigger(s: str) -> str:
    t = (s or "unknown").strip().lower()
    if t in ("confirmed", "waiting_for_pullback", "waiting_for_choch", "invalid", "unknown"):
        return t
    return t if t else "unknown"


def format_trigger_confirmed_telegram_zh(symbol: str, rep: dict[str, Any]) -> str:
    tfd = rep.get("timeframes") or {}
    d = tfd.get("daily") or {}
    h4 = tfd.get("4h") or {}
    t30 = tfd.get("30min") or {}
    t5 = tfd.get("5min") or {}
    lines = [
        "【MTF SMC/ICT 5分钟触发确认】",
        f"Symbol: {symbol}",
        "状态：5min trigger confirmed",
        f"Daily Bias: {d.get('bias', '-')}",
        f"4H Structure: {h4.get('structure', '-')}",
        f"30min Setup: {t30.get('setup_state', '-')}",
        f"5min Trigger: {t5.get('trigger_state', '-')}",
        f"Entry: {t30.get('entry_price', '-')}",
        f"Stop: {t30.get('stop_price', '-')}",
        f"Target: {t30.get('target_1', '-')}",
        f"R/R: {t30.get('risk_reward', '-')}",
        "说明：该标的已从 near-alignment 进入 FULL_ALIGNMENT / trigger confirmed 状态。",
        "提醒：当前仅提醒，不下单。paper bracket gate 未开启。",
    ]
    return "\n".join(lines)


def format_still_waiting_telegram_zh(
    items: list[tuple[str, str]],
) -> str:
    """``items`` = (symbol, current_trigger_state)."""
    lines: list[str] = ["【MTF SMC/ICT 触发观察中】"]
    for sym, tstate in items:
        lines.append(f"{sym}：仍等待 5m sweep + ChoCH + FVG / displacement。")
        lines.append(f"当前状态：{tstate}")
    lines.append("系统未下单。")
    return "\n".join(lines)


def format_no_trigger_watch_telegram_zh() -> str:
    return "\n".join(
        [
            "【MTF SMC/ICT 触发观察】",
            "当前没有 FIVE_MIN_TRIGGER 观察候选。系统未下单。",
        ]
    )


def _item_message_zh(
    symbol: str,
    prev_state: str,
    cur_state: str,
    state_changed: bool,
) -> str:
    if _normalise_trigger(cur_state) == "confirmed":
        return f"{symbol}：5m 触发已确认（{prev_state} → {cur_state}）。"
    if state_changed:
        return f"{symbol}：{prev_state} → {cur_state}（仍观察 5m）。"
    return f"{symbol}：5m 状态 {cur_state}，继续观察。"


# --------------------------------------------------------------------------- #
# One-shot run
# --------------------------------------------------------------------------- #

WatchFetchFn = Callable[[str, AppConfig, bool, bool], tuple[Any, list[str], Any]]


def run_mtf_trigger_check(
    cfg: AppConfig,
    journal: Journal,
    *,
    mtf_dir: Path,
    report_date: str,
    use_ibkr: bool,
    top: int,
    include_premium: bool,
    symbol_filter: str | None,
    telegram: bool,
    state_path: Path,
    empty_digest_telegram: bool = True,
    # injectable for tests
    connect_fetch: WatchFetchFn | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one pass: refresh IBKR data, re-run :func:`run_mtf_smc`, update state.

    Returns ``(output_json, private_meta)`` with ``private_meta`` containing
    ``mreps``, ``per_symbol_telegram_confirmed``, and ``waiting_syms`` for
    follow-up Telegram in watch mode.

    If ``use_ibkr`` is False, the caller (CLI) should exit before this;
    the parameter kept for test doubles that bypass fetch.
    """
    if connect_fetch is None:
        from .cli import _mtf_connect_and_fetch

        connect_fetch = _mtf_connect_and_fetch  # type: ignore[assignment]
    from .cli import _resolve_regime_context

    rep_path = mtf_dir / f"{report_date}-mtf-diagnostic-report.json"
    if not rep_path.exists():
        raise FileNotFoundError(str(rep_path))
    drep = load_mtf_json(rep_path)
    near = list(drep.get("near_alignment_candidates") or [])
    active, _secondary = select_trigger_watch_candidates(
        near, include_premium=include_premium, top=top, symbol=symbol_filter
    )
    now = datetime.now(timezone.utc)
    checked_at = now.isoformat()
    out_base: dict[str, Any] = {
        "date": report_date,
        "checked_at": checked_at,
        "research_only": True,
        "execution_allowed": False,
        "symbols_checked": 0,
        "trigger_confirmed": [],
        "still_waiting": [],
        "state_changes": [],
        "items": [],
    }
    rstore = load_runtime_trigger_state(state_path)
    regime_ctx = _resolve_regime_context(cfg, None)
    regime = str(regime_ctx["market_regime"])
    conf = str(regime_ctx.get("regime_confidence") or "medium")

    if not active:
        if (
            empty_digest_telegram
            and telegram
            and cfg.telegram.is_configured
        ):
            body = f"<pre>{escape(format_no_trigger_watch_telegram_zh())}</pre>"
            send_telegram_message(body, cfg=cfg, journal=journal)
        out = {**out_base, "items": []}
        p_out = mtf_dir / f"{report_date}-trigger-check.json"
        p_out.write_text(
            json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return out, {
            "mreps": {},
            "per_symbol_telegram_confirmed": {},
            "waiting_syms": [],
        }

    confirmed_syms: list[str] = []
    waiting_syms: list[tuple[str, str]] = []
    state_change_labels: list[str] = []
    items: list[dict[str, Any]] = []
    mreps: dict[str, dict[str, Any]] = {}
    per_symbol_telegram_confirmed: dict[str, bool] = {}

    for cand in active:
        sym = str(cand.get("symbol", "")).upper()
        prev_bl = str(cand.get("blocking_layer", ""))
        st_prev = rstore.symbols.get(sym)
        if st_prev is None:
            p_ts = _previous_trigger_from_saved_mtf(mtf_dir, report_date, sym)
            st_old = SymbolRuntimeState(
                last_trigger_state=p_ts,
                last_alignment_category=str(cand.get("alignment_category", "")),
                last_score=int(cand.get("mtf_alignment_score", 0) or 0),
                last_eligible=bool(
                    cand.get("eligible_for_future_paper_trade", False)
                ),
            )
        else:
            p_ts = st_prev.last_trigger_state
            st_old = st_prev
        b = MtfCandleBundle()
        w: list[str] = []
        client = None
        if use_ibkr:
            b, w, client = connect_fetch(
                sym, cfg, include_5min=True, include_daily=True
            )
            if client is not None:
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        out_ev: dict[str, Any] = {}
        mrep = run_mtf_smc(
            sym,
            cfg,
            b,
            market_regime=regime,
            regime_confidence=conf,
            include_5min=True,
            include_daily=True,
            out_eval=out_ev,
        )
        mrep["warnings"] = list(
            dict.fromkeys((mrep.get("warnings") or []) + w)
        )
        mreps[sym] = mrep
        t5 = (mrep.get("timeframes") or {}).get("5min") or {}
        cur_t = str(t5.get("trigger_state", "unknown"))
        cur_t_n = _normalise_trigger(cur_t)
        cat = str(mrep.get("alignment_category", ""))
        sc = int(mrep.get("mtf_alignment_score") or 0)
        elig = bool(mrep.get("eligible_for_future_paper_trade", False))
        state_changed = _meaningful_state_change(
            st_old,
            cur_trigger=cur_t_n,
            cur_cat=cat,
            cur_elig=elig,
            cur_score=sc,
        )
        if cur_t_n == "confirmed":
            confirmed_syms.append(sym)
        else:
            waiting_syms.append((sym, cur_t_n))

        dup_ok = (
            cur_t_n == "confirmed"
            and st_prev
            and st_prev.last_trigger_state == "confirmed"
            and st_prev.confirmed_alert_sent_for_date == report_date
        )
        per_symbol_telegram_confirmed[sym] = bool(
            cur_t_n == "confirmed" and not dup_ok
        )

        item = {
            "symbol": sym,
            "previous_blocking_layer": prev_bl,
            "previous_trigger_state": p_ts,
            "current_trigger_state": cur_t_n,
            "alignment_category": cat,
            "mtf_alignment_score": sc,
            "eligible_for_future_paper_trade": elig,
            "state_changed": state_changed,
            "message_zh": _item_message_zh(sym, p_ts, cur_t_n, state_changed),
            "chart_paths": list(mrep.get("chart_paths") or []),
        }
        if state_changed:
            state_change_labels.append(sym)
        items.append(item)

        day_key = report_date
        new_st = SymbolRuntimeState(
            last_trigger_state=cur_t_n,
            last_alignment_category=cat,
            last_score=sc,
            last_eligible=elig,
            last_checked_at=checked_at,
            confirmed_alert_sent_for_date=getattr(
                st_old, "confirmed_alert_sent_for_date", ""
            )
            or "",
        )
        if cur_t_n == "confirmed":
            if st_old.last_trigger_state != "confirmed":
                new_st.confirmed_alert_sent_for_date = day_key
            else:
                new_st.confirmed_alert_sent_for_date = (
                    st_old.confirmed_alert_sent_for_date
                )
        else:
            if st_old.last_trigger_state == "confirmed" and cur_t_n != "confirmed":
                new_st.confirmed_alert_sent_for_date = ""
        rstore.symbols[sym] = new_st
        out_base["symbols_checked"] = out_base.get("symbols_checked", 0) + 1

    out_base["items"] = items
    out_base["trigger_confirmed"] = confirmed_syms
    out_base["still_waiting"] = [s for s, _ in waiting_syms]
    out_base["state_changes"] = state_change_labels

    save_runtime_trigger_state(state_path, rstore)

    p_out = mtf_dir / f"{report_date}-trigger-check.json"
    p_out.write_text(
        json.dumps(out_base, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    if telegram and cfg.telegram.is_configured:
        for it in items:
            sym = it["symbol"]
            if str(it["current_trigger_state"]) != "confirmed":
                continue
            if not per_symbol_telegram_confirmed.get(sym):
                continue
            txt = format_trigger_confirmed_telegram_zh(sym, mreps[sym])
            body = f"<pre>{escape(txt)}</pre>"
            send_telegram_message(body, cfg=cfg, journal=journal)
        oneshot_still = still_waiting_telegram_worthy(
            is_oneshot=True,
            is_watch=False,
            first_cycle=True,
            any_state_change=any(
                bool(x.get("state_changed")) for x in items
            ),
            seconds_since_heartbeat=0.0,
        )
        if waiting_syms and oneshot_still:
            stxt = format_still_waiting_telegram_zh(waiting_syms)
            bodyw = f"<pre>{escape(stxt)}</pre>"
            send_telegram_message(bodyw, cfg=cfg, journal=journal)
    meta = {
        "mreps": mreps,
        "per_symbol_telegram_confirmed": per_symbol_telegram_confirmed,
        "waiting_syms": waiting_syms,
    }
    return out_base, meta


def still_waiting_telegram_worthy(
    *,
    is_oneshot: bool,
    is_watch: bool,
    first_cycle: bool,
    any_state_change: bool,
    seconds_since_heartbeat: float,
    min_heartbeat_seconds: float = 1800.0,
) -> bool:
    """Still-waiting summary must not spam: allowed per Prompt 10F rules."""
    if is_oneshot and not is_watch:
        return True
    if not is_watch:
        return bool(is_oneshot)
    if first_cycle:
        return True
    if any_state_change:
        return True
    if seconds_since_heartbeat >= min_heartbeat_seconds:
        return True
    return False


def run_mtf_trigger_watch_loop(
    cfg: AppConfig,
    journal: Journal,
    *,
    mtf_dir: Path,
    report_date: str,
    use_ibkr: bool,
    top: int,
    include_premium: bool,
    symbol_filter: str | None,
    telegram: bool,
    state_path: Path,
    interval_minutes: int,
    duration_minutes: int,
    connect_fetch: WatchFetchFn | None = None,
    log_path: Path | None = None,
    time_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    log_path = log_path or (mtf_dir / f"{report_date}-trigger-watch.jsonl")
    mtf_dir.mkdir(parents=True, exist_ok=True)
    end_ts = time_fn() + max(0, int(duration_minutes)) * 60.0
    cycle = 0
    last_still_heartbeat: float = 0.0

    def _log(ev: dict[str, Any]) -> None:
        if log_path:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    while time_fn() < end_ts:
        cycle += 1
        first = cycle == 1
        try:
            t_before = time_fn()
            out, meta = run_mtf_trigger_check(
                cfg,
                journal,
                mtf_dir=mtf_dir,
                report_date=report_date,
                use_ibkr=use_ibkr,
                top=top,
                include_premium=include_premium,
                symbol_filter=symbol_filter,
                telegram=False,
                state_path=state_path,
                empty_digest_telegram=(cycle == 1),
                connect_fetch=connect_fetch,
            )
            mreps = meta.get("mreps") or {}
            per_ok = meta.get("per_symbol_telegram_confirmed") or {}
            waiting_syms = meta.get("waiting_syms") or []
            n_items = int(out.get("symbols_checked", 0) or 0)
            tconf = len(out.get("trigger_confirmed") or [])
            nchg = len(out.get("state_changes") or [])
            items_l = out.get("items") or []
            any_ch = any(bool(x.get("state_changed")) for x in items_l)
            sec_since = t_before - last_still_heartbeat if last_still_heartbeat else 1.0e9
            if telegram and cfg.telegram.is_configured:
                for it in items_l:
                    sym = str(it.get("symbol", ""))
                    if str(it.get("current_trigger_state")) != "confirmed":
                        continue
                    if not per_ok.get(sym):
                        continue
                    txt = format_trigger_confirmed_telegram_zh(
                        sym, mreps.get(sym) or {}
                    )
                    send_telegram_message(
                        f"<pre>{escape(txt)}</pre>",
                        cfg=cfg,
                        journal=journal,
                    )
                if still_waiting_telegram_worthy(
                    is_oneshot=False,
                    is_watch=True,
                    first_cycle=first,
                    any_state_change=any_ch,
                    seconds_since_heartbeat=sec_since,
                ) and bool(waiting_syms):
                    stxt = format_still_waiting_telegram_zh(
                        list(waiting_syms)
                    )
                    send_telegram_message(
                        f"<pre>{escape(stxt)}</pre>",
                        cfg=cfg,
                        journal=journal,
                    )
                    last_still_heartbeat = t_before
            _log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle,
                    "symbols_checked": n_items,
                    "trigger_confirmed_count": tconf,
                    "state_changes_count": nchg,
                    "status": "success",
                    "execution_allowed": False,
                    "research_only": True,
                }
            )
        except KeyboardInterrupt:
            _log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle,
                    "symbols_checked": 0,
                    "trigger_confirmed_count": 0,
                    "state_changes_count": 0,
                    "status": "skipped",
                    "message": "interrupted",
                    "execution_allowed": False,
                    "research_only": True,
                }
            )
            raise
        except Exception as exc:  # noqa: BLE001
            err = traceback.format_exc()
            _log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle,
                    "symbols_checked": 0,
                    "trigger_confirmed_count": 0,
                    "state_changes_count": 0,
                    "status": "failed",
                    "error": str(exc),
                    "trace": err,
                    "execution_allowed": False,
                    "research_only": True,
                }
            )
        if time_fn() >= end_ts:
            break
        sleep_fn(float(max(1, int(interval_minutes) * 60)))


__all__ = [
    "RUNTIME_STATE_FILENAME",
    "RuntimeTriggerStore",
    "SymbolRuntimeState",
    "find_latest_diagnostic_report_path",
    "format_no_trigger_watch_telegram_zh",
    "format_still_waiting_telegram_zh",
    "format_trigger_confirmed_telegram_zh",
    "load_runtime_trigger_state",
    "run_mtf_trigger_check",
    "run_mtf_trigger_watch_loop",
    "save_runtime_trigger_state",
    "select_trigger_watch_candidates",
    "still_waiting_telegram_worthy",
]
