"""Strategy selection file (13STRATEGY-UI) — no broker."""

from __future__ import annotations

import json
from pathlib import Path

from bot.strategy_ui import (
    load_strategy_ui_catalog,
    load_strategy_selection,
    save_strategy_selection,
    selection_from_mapping,
    selection_path,
    DEFAULT_STRATEGY_ID,
)


def test_missing_file_defaults_to_ict(tmp_path: Path) -> None:
    cat = load_strategy_ui_catalog(tmp_path)
    st = load_strategy_selection(tmp_path, catalog=cat)
    assert st.active_paper_strategy == DEFAULT_STRATEGY_ID
    assert st.active_scan_strategy == DEFAULT_STRATEGY_ID
    p = selection_path(tmp_path)
    assert not p.exists()


def test_invalid_strategy_reverts_in_coerce(tmp_path: Path) -> None:
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    p = selection_path(tmp_path)
    p.write_text(
        json.dumps(
            {
                "active_paper_strategy": "not_a_real_id",
                "active_scan_strategy": "not_a_real_id",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cat = load_strategy_ui_catalog(tmp_path)
    st = load_strategy_selection(tmp_path, catalog=cat)
    assert st.active_paper_strategy == DEFAULT_STRATEGY_ID
    assert st.last_warnings


def test_paper_cannot_stay_on_non_paper_enabled(tmp_path: Path) -> None:
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    p = selection_path(tmp_path)
    p.write_text(
        json.dumps({"active_paper_strategy": "chanlun_intraday_v1"}) + "\n",
        encoding="utf-8",
    )
    cat = load_strategy_ui_catalog(tmp_path)
    st = load_strategy_selection(tmp_path, catalog=cat)
    assert st.active_paper_strategy == DEFAULT_STRATEGY_ID


def test_save_round_trip_ict_paper(tmp_path: Path) -> None:
    cat = load_strategy_ui_catalog(tmp_path)
    cur, _ = selection_from_mapping(
        cat, {"active_paper_strategy": "ict_smc_intraday_v1"}, current=None
    )
    out = save_strategy_selection(tmp_path, cur, catalog=cat)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["active_paper_strategy"] == "ict_smc_intraday_v1"
