"""Prompt 10F: MTF 5m trigger watch (alert-only, no orders)."""

from __future__ import annotations

import json
import yaml
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from bot.config import load_config
from bot.journal import Journal
from bot.mtf_smc_engine import MtfCandleBundle
from bot.mtf_trigger_watch import (
    SymbolRuntimeState,
    _meaningful_state_change,
    find_latest_diagnostic_report_path,
    format_no_trigger_watch_telegram_zh,
    format_still_waiting_telegram_zh,
    format_trigger_confirmed_telegram_zh,
    load_runtime_trigger_state,
    run_mtf_trigger_check,
    run_mtf_trigger_watch_loop,
    save_runtime_trigger_state,
    select_trigger_watch_candidates,
    still_waiting_telegram_worthy,
    RUNTIME_STATE_FILENAME,
)


def test_selects_five_min_first() -> None:
    near = [
        {
            "symbol": "Z",
            "blocking_layer": "THIRTY_MIN_SETUP",
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "mtf_alignment_score": 60,
        },
        {
            "symbol": "A",
            "blocking_layer": "FIVE_MIN_TRIGGER",
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "mtf_alignment_score": 50,
        },
    ]
    act, _s = select_trigger_watch_candidates(
        near, include_premium=True, top=10
    )
    assert act[0]["symbol"] == "A"


def test_select_excludes_risk_and_daily_blocks() -> None:
    for bl in ("RISK", "DAILY_BIAS"):
        near = [
            {
                "symbol": "X",
                "blocking_layer": bl,
                "alignment_category": "SETUP_READY_WAITING_TRIGGER",
                "mtf_alignment_score": 60,
            },
        ]
        act, _s = select_trigger_watch_candidates(
            near, include_premium=True, top=10
        )
        assert act == []


def test_premium_excluded_without_flag() -> None:
    near = [
        {
            "symbol": "AMZN",
            "blocking_layer": "PREMIUM_DISCOUNT",
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "mtf_alignment_score": 45,
        },
    ]
    act, sec = select_trigger_watch_candidates(
        near, include_premium=False, top=5
    )
    assert act == [] and len(sec) == 1
    act2, _s2 = select_trigger_watch_candidates(
        near, include_premium=True, top=5
    )
    assert len(act2) == 1


def test_mtf_trigger_check_writes_json(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-04-20"
    near = [
        {
            "symbol": "CRM",
            "mtf_alignment_score": 60,
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "blocking_layer": "FIVE_MIN_TRIGGER",
            "primary_missing_condition": "x",
            "next_condition_to_watch": "y",
            "eligible_for_future_paper_trade": False,
        },
    ]
    rep = {"date": d, "near_alignment_candidates": near, "items": []}
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(rep, ensure_ascii=False), encoding="utf-8"
    )
    state_path = mtf / RUNTIME_STATE_FILENAME
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(50)]

    def fake_smc(
        _sym, _cfg, _b, **kw
    ):  # noqa: ANN202
        return {
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {
                    "setup_state": "full_setup_valid",
                    "entry_price": 1.0,
                    "stop_price": 0.5,
                    "target_1": 2.0,
                    "risk_reward": 1.5,
                },
                "5min": {"loaded": True, "trigger_state": "waiting_for_pullback"},
            },
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "mtf_alignment_score": 60,
            "eligible_for_future_paper_trade": False,
            "chart_paths": [],
        }

    def fake_fetch(
        s: str, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        b = MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5)
        return b, [], None

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    out, _meta = run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=state_path,
        connect_fetch=fake_fetch,
    )
    outpath = mtf / f"{d}-trigger-check.json"
    assert outpath.is_file()
    pl = json.loads(outpath.read_text(encoding="utf-8"))
    assert pl.get("research_only") is True
    assert pl.get("execution_allowed") is False
    assert int(pl.get("symbols_checked") or 0) == 1
    assert pl.get("items") and pl["items"][0]["symbol"] == "CRM"


def test_format_trigger_confirmed_telegram_high_priority() -> None:
    r = {
        "timeframes": {
            "daily": {"bias": "bullish"},
            "4h": {"structure": "bullish_confirmed"},
            "30min": {
                "setup_state": "full_setup_valid",
                "entry_price": 10.0,
                "stop_price": 9.0,
                "target_1": 12.0,
                "risk_reward": 1.2,
            },
            "5min": {"trigger_state": "confirmed"},
        },
    }
    t = format_trigger_confirmed_telegram_zh("CRM", r)
    assert "【MTF SMC/ICT 5分钟触发确认】" in t
    assert "paper bracket gate" in t
    assert "5min trigger confirmed" in t or "confirmed" in t


def test_still_waiting_not_spam() -> None:
    assert not still_waiting_telegram_worthy(
        is_oneshot=False,
        is_watch=True,
        first_cycle=False,
        any_state_change=False,
        seconds_since_heartbeat=10.0,
    )
    assert still_waiting_telegram_worthy(
        is_oneshot=False,
        is_watch=True,
        first_cycle=True,
        any_state_change=False,
        seconds_since_heartbeat=0.0,
    )
    assert still_waiting_telegram_worthy(
        is_oneshot=False,
        is_watch=True,
        first_cycle=False,
        any_state_change=False,
        seconds_since_heartbeat=2000.0,
    )


def test_state_change_detection_score() -> None:
    prev = SymbolRuntimeState(
        last_trigger_state="waiting_for_pullback",
        last_alignment_category="X",
        last_score=50,
    )
    assert _meaningful_state_change(
        prev,
        cur_trigger="waiting_for_pullback",
        cur_cat="X",
        cur_elig=False,
        cur_score=61,
    )
    assert not _meaningful_state_change(
        prev,
        cur_trigger="waiting_for_pullback",
        cur_cat="X",
        cur_elig=False,
        cur_score=55,
    )


def test_no_duplicate_confirmed_telegram_meta(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-04-21"
    near = [
        {
            "symbol": "CRM",
            "mtf_alignment_score": 80,
            "alignment_category": "FULL_ALIGNMENT",
            "blocking_layer": "FIVE_MIN_TRIGGER",
            "primary_missing_condition": "x",
            "next_condition_to_watch": "y",
            "eligible_for_future_paper_trade": False,
        },
    ]
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(
            {"date": d, "near_alignment_candidates": near, "items": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(50)]

    def _mk_rep():
        return {
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {
                    "setup_state": "full_setup_valid",
                    "entry_price": 1.0,
                    "stop_price": 0.5,
                    "target_1": 2.0,
                    "risk_reward": 1.5,
                },
                "5min": {"loaded": True, "trigger_state": "confirmed"},
            },
            "alignment_category": "FULL_ALIGNMENT",
            "mtf_alignment_score": 80,
            "eligible_for_future_paper_trade": True,
            "chart_paths": [],
        }

    def fake_smc(
        _sym, _cfg, _b, **kw
    ):  # noqa: ANN202
        return _mk_rep()

    def fake_fetch(
        s: str, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        b = MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5)
        return b, [], None

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    state_path = mtf / RUNTIME_STATE_FILENAME
    _a, meta1 = run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=state_path,
        connect_fetch=fake_fetch,
    )
    assert meta1["per_symbol_telegram_confirmed"].get("CRM") is True
    _a2, meta2 = run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=state_path,
        connect_fetch=fake_fetch,
    )
    assert meta2["per_symbol_telegram_confirmed"].get("CRM") is False


def test_watch_loop_logs_jsonl(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-04-22"
    near: list[dict] = [
        {
            "symbol": "A",
            "mtf_alignment_score": 50,
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "blocking_layer": "FIVE_MIN_TRIGGER",
            "primary_missing_condition": "x",
            "next_condition_to_watch": "y",
            "eligible_for_future_paper_trade": False,
        },
    ]
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(
            {"date": d, "near_alignment_candidates": near, "items": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(30)]

    def fake_smc(
        _s, _c, _b, **kw
    ):  # noqa: ANN202
        return {
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {
                    "setup_state": "incomplete",
                    "entry_price": None,
                },
                "5min": {"loaded": True, "trigger_state": "waiting_for_pullback"},
            },
            "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
            "mtf_alignment_score": 40,
            "eligible_for_future_paper_trade": False,
            "chart_paths": [],
        }

    def fake_fetch(
        s: str, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        return MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5), [], None

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    logp = mtf / f"{d}-trigger-watch.jsonl"
    t = [0.0]

    def time_fn() -> float:
        return t[0]

    def sleep_fn(_: float) -> None:
        t[0] = 1_000.0  # end loop after one sleep (duration window exceeded)

    run_mtf_trigger_watch_loop(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=mtf / RUNTIME_STATE_FILENAME,
        interval_minutes=1,
        duration_minutes=1,
        connect_fetch=fake_fetch,
        log_path=logp,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
    )
    text = logp.read_text(encoding="utf-8")
    line = [ln for ln in text.strip().splitlines() if ln][-1]
    ev = json.loads(line)
    assert ev["status"] == "success"
    assert ev["execution_allowed"] is False
    assert ev["research_only"] is True


def test_watch_loop_keyboard_interrupt(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-04-23"
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(
            {
                "date": d,
                "near_alignment_candidates": [
                    {
                        "symbol": "X",
                        "mtf_alignment_score": 50,
                        "alignment_category": "SETUP_READY_WAITING_TRIGGER",
                        "blocking_layer": "FIVE_MIN_TRIGGER",
                    },
                ],
                "items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(20)]

    def fake_smc(
        _s, _c, _b, **kw
    ):  # noqa: ANN202
        return {
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {"setup_state": "incomplete"},
                "5min": {"loaded": True, "trigger_state": "unknown"},
            },
            "alignment_category": "BLOCKED",
            "mtf_alignment_score": 20,
            "eligible_for_future_paper_trade": False,
            "chart_paths": [],
        }

    def fake_fetch(
        s: str, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        return MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5), [], None

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    n = 0

    def sleep_fn(_: float) -> None:
        nonlocal n
        n += 1
        raise KeyboardInterrupt

    t = [0.0]

    def time_fn() -> float:
        return t[0] if t[0] < 2_000 else 3_000.0  # end while if needed

    with pytest.raises(KeyboardInterrupt):
        run_mtf_trigger_watch_loop(
            cfg,
            journal,
            mtf_dir=mtf,
            report_date=d,
            use_ibkr=True,
            top=5,
            include_premium=False,
            symbol_filter=None,
            telegram=False,
            state_path=mtf / RUNTIME_STATE_FILENAME,
            interval_minutes=1,
            duration_minutes=10,
            connect_fetch=fake_fetch,
            log_path=mtf / f"{d}-w.jsonl",
            time_fn=time_fn,
            sleep_fn=sleep_fn,
        )


def test_no_broker_place_order_on_trigger_check_cli(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot import broker as broker_mod
    from bot import cli as cli_mod

    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-04-24"
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(
        broker_mod.Broker, "place_order", MagicMock(
            side_effect=AssertionError("place_order")
        )
    )

    def _fake(*_a, **_k):
        return {
            "date": d,
            "checked_at": "x",
            "research_only": True,
            "execution_allowed": False,
            "symbols_checked": 0,
            "trigger_confirmed": [],
            "still_waiting": [],
            "state_changes": [],
            "items": [],
        }, {
            "mreps": {},
            "per_symbol_telegram_confirmed": {},
            "waiting_syms": [],
        }

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_trigger_check", _fake)
    monkeypatch.setattr(
        cli_mod, "_bootstrap",
        lambda: (load_config(project_root=tmp_project), MagicMock()),
    )
    r = CliRunner().invoke(
        cli_mod.app,
        ["mtf-trigger-check", "--date", d, "--ibkr"],
    )
    assert r.exit_code == 0


def test_config_unchanged_after_trigger_helper(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s1 = yaml.safe_load(
        (tmp_project / "config" / "settings.yaml").read_text()
    )["trading"]
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-01-10"
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(
            {
                "date": d,
                "near_alignment_candidates": [
                    {
                        "symbol": "A",
                        "mtf_alignment_score": 50,
                        "alignment_category": "SETUP_READY_WAITING_TRIGGER",
                        "blocking_layer": "FIVE_MIN_TRIGGER",
                    },
                ],
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(20)]

    def fake_smc(
        _s, _c, _b, **kw
    ):  # noqa: ANN202
        return {
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {"setup_state": "incomplete"},
                "5min": {"loaded": True, "trigger_state": "unknown"},
            },
            "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
            "mtf_alignment_score": 40,
            "eligible_for_future_paper_trade": False,
        }

    def fake_fetch(
        s, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        return MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5), [], None

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=mtf / "runtime.json",
        connect_fetch=fake_fetch,
    )
    s2 = yaml.safe_load(
        (tmp_project / "config" / "settings.yaml").read_text()
    )["trading"]
    assert s1.get("mtf_paper_dry_run") == s2.get("mtf_paper_dry_run")
    assert s1.get("mtf_paper_bracket_enabled") == s2.get("mtf_paper_bracket_enabled")
    assert s1.get("enabled") == s2.get("enabled")


def test_10g_auto_paper_bracket_calls_paper_path(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_bracket_enabled"] = True
    s["trading"]["mtf_paper_auto_bracket_enabled"] = True
    s["trading"]["mtf_paper_dry_run"] = True
    s["account"]["mode"] = "paper"
    write_yaml(p, s)
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    d = "2026-05-01"
    near = [
        {
            "symbol": "ZZZ",
            "mtf_alignment_score": 90,
            "alignment_category": "FULL_ALIGNMENT",
            "blocking_layer": "FIVE_MIN_TRIGGER",
        },
    ]
    (mtf / f"{d}-mtf-diagnostic-report.json").write_text(
        json.dumps(
            {"date": d, "near_alignment_candidates": near, "items": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c5 = [{"open": 1, "high": 1, "low": 1, "close": 1, "date": f"t{i}"} for i in range(50)]

    def fake_smc(
        _sym, _cfg, _b, **kw
    ):  # noqa: ANN202
        return {
            "symbol": "ZZZ",
            "timeframes": {
                "daily": {"bias": "bullish"},
                "4h": {"structure": "bullish_confirmed"},
                "30min": {
                    "setup_state": "full_setup_valid",
                    "entry_price": 10.0,
                    "stop_price": 9.0,
                    "target_1": 12.0,
                },
                "5min": {"loaded": True, "trigger_state": "confirmed"},
            },
            "alignment_category": "FULL_ALIGNMENT",
            "mtf_alignment_score": 90,
            "eligible_for_future_paper_trade": True,
            "chart_paths": [],
        }

    def fake_fetch(
        s, c, *, include_5min: bool, include_daily: bool
    ) -> tuple[MtfCandleBundle, list[str], object]:
        return MtfCandleBundle(m5=c5, m30=c5, h4=c5, daily=c5), [], None

    n_calls = 0

    def fake_paper(
        _cfg, _journal, mrep, **_k
    ):  # noqa: ANN202
        nonlocal n_calls
        n_calls += 1
        return {
            "submitted": False,
            "order_ids": [],
            "error": None,
        }

    monkeypatch.setattr("bot.mtf_trigger_watch.run_mtf_smc", fake_smc)
    monkeypatch.setattr(
        "bot.mtf_paper_execution.connect_and_run_mtf_paper_bracket",
        fake_paper,
    )
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    out, _m = run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf,
        report_date=d,
        use_ibkr=True,
        top=5,
        include_premium=False,
        symbol_filter=None,
        telegram=False,
        state_path=mtf / RUNTIME_STATE_FILENAME,
        auto_paper_bracket=True,
        connect_fetch=fake_fetch,
    )
    ar = out.get("auto_paper_bracket_runs") or []
    assert n_calls == 1
    assert ar and ar[0].get("symbol") == "ZZZ"


def test_format_no_five_telegram_text() -> None:
    t = format_no_trigger_watch_telegram_zh()
    assert "FIVE" in t
    assert "未下单" in t


def test_still_waiting_digest_lines() -> None:
    t = format_still_waiting_telegram_zh(
        [("CRM", "waiting_for_pullback")]
    )
    assert "系统未下单" in t
    assert "触发观察" in t


def test_find_latest_diagnostic_path(tmp_project: Path) -> None:
    mtf = tmp_project / "data" / "mtf_smc"
    mtf.mkdir(parents=True, exist_ok=True)
    p = mtf / "2026-02-10-mtf-diagnostic-report.json"
    p.write_text("{}", encoding="utf-8")
    p.touch()
    r = find_latest_diagnostic_report_path(mtf)
    assert r is not None
    assert r[0] == "2026-02-10" and r[1] == p


def test_runtime_roundtrip(tmp_project: Path) -> None:
    p = tmp_project / "x.json"
    st = load_runtime_trigger_state(p)
    st.symbols["A"] = SymbolRuntimeState(last_trigger_state="x")
    save_runtime_trigger_state(p, st)
    st2 = load_runtime_trigger_state(p)
    assert st2.symbols["A"].last_trigger_state == "x"
