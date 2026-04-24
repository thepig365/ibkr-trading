"""Tests for :mod:`bot.tws_watchlist_export` (Prompt 9.3).

Verified invariants:

* Dated CSV + TXT and the ``latest-*`` aliases are all created and
  atomically rewritten on every run.
* CSV contains the full column set from the prompt and the example
  row values survive the round-trip.
* Missing optional metric fields are rendered as empty strings instead
  of blowing up the exporter.
* Offline ``PrimaryExchange`` inference attaches a warning for unknown
  symbols and resolves the obvious NASDAQ / ARCA tickers deterministically.
* IBKR contract validation populates ``ConId`` /
  ``ContractValidated=true`` when ``qualifyContracts`` returns a hit,
  and degrades to offline inference (with a warning) otherwise.
* Blocked rows are excluded from the TWS export by default so an
  import into TWS cannot silently pick up filtered symbols.
* ``build-watchlist`` auto-exports the TWS artefacts.
* ``run-opening-review`` includes ``export-tws-watchlist`` and the
  command never reaches :func:`bot.broker.Broker.place_order`.
* Every CLI / journal surface carries ``execution_allowed=false``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from bot.tws_watchlist_export import (
    TWS_CSV_COLUMNS,
    ValidatedContract,
    build_tws_rows,
    export_tws_watchlist,
    infer_primary_exchange,
    load_watchlist_by_date_or_latest,
    validate_contracts,
)
from bot.watchlist_builder import (
    DynamicWatchlist,
    WatchlistCandidate,
    save_dynamic_watchlist,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_candidate(
    symbol: str,
    *,
    price: float | None = 250.0,
    reason: list[str] | None = None,
    blocked: bool = False,
    block_reason: str | None = None,
    cur_dv: float | None = 4_262_614_290.31,
    avg_dv: float | None = 5_762_269_452.66,
    rel: float | None = 0.7397,
    atr: float | None = 2.2949,
    rv: float | None = 25.8463,
    score: float | None = 0.216817,
    activity: str = "normal_activity",
) -> WatchlistCandidate:
    return WatchlistCandidate(
        symbol=symbol,
        reason=list(reason or ["static_core", "high_current_dollar_volume"]),
        latest_price=price,
        current_dollar_volume=cur_dv,
        avg_20d_dollar_volume=avg_dv,
        relative_volume=rel,
        volume_activity=activity,
        atr_pct=atr,
        realized_vol_20d=rv,
        volume_rank_score=score,
        blocked=blocked,
        block_reason=block_reason,
    )


@pytest.fixture
def tiny_watchlist() -> DynamicWatchlist:
    return DynamicWatchlist(
        date="2026-04-24",
        source="ibkr",
        symbols=[
            _make_candidate("AAPL"),
            _make_candidate("NVDA", price=1420.0),
            _make_candidate("SPY", price=500.0, reason=["static_core"]),
            _make_candidate(
                "TQQQ",
                blocked=True,
                block_reason="leveraged_etf_excluded",
                reason=["high_current_dollar_volume"],
            ),
        ],
        missing_data=[],
    )


# ---------------------------------------------------------------------------
# infer_primary_exchange
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sym, expected_exchange",
    [("AAPL", "NASDAQ"), ("NVDA", "NASDAQ"), ("TSLA", "NASDAQ"),
     ("SPY", "ARCA"), ("QQQ", "ARCA"), ("IWM", "ARCA")],
)
def test_infer_primary_exchange_known(sym: str, expected_exchange: str) -> None:
    primary, warn = infer_primary_exchange(sym)
    assert primary == expected_exchange
    assert warn == ""


def test_infer_primary_exchange_unknown_returns_warning() -> None:
    primary, warn = infer_primary_exchange("ZZZZZ")
    assert primary == ""
    assert "primary_exchange_unknown" in warn


# ---------------------------------------------------------------------------
# build_tws_rows
# ---------------------------------------------------------------------------
def test_build_tws_rows_covers_expected_columns(
    tiny_watchlist: DynamicWatchlist,
) -> None:
    rows = build_tws_rows(tiny_watchlist)
    # 3 non-blocked rows (AAPL, NVDA, SPY).
    assert [r["Symbol"] for r in rows] == ["AAPL", "NVDA", "SPY"]
    for row in rows:
        assert set(TWS_CSV_COLUMNS).issubset(row.keys())
        assert row["SecType"] == "STK"
        assert row["Exchange"] == "SMART"
        assert row["Currency"] == "USD"
    # The AAPL row matches the prompt's example row.
    aapl = rows[0]
    assert aapl["LatestPrice"] == "250.0"
    assert aapl["CurrentDollarVolume"] == "4262614290.31"
    assert aapl["Avg20DDollarVolume"] == "5762269452.66"
    assert aapl["RelativeVolume"] == "0.7397"
    assert aapl["VolumeActivity"] == "normal_activity"
    assert aapl["ATRPercent"] == "2.2949"
    assert aapl["RealizedVol20D"] == "25.8463"
    assert aapl["RankScore"] == "0.216817"
    assert "static_core" in aapl["Reason"]
    assert aapl["PrimaryExchange"] == "NASDAQ"
    assert aapl["ContractValidated"] == "false"


def test_build_tws_rows_excludes_blocked_by_default(
    tiny_watchlist: DynamicWatchlist,
) -> None:
    rows = build_tws_rows(tiny_watchlist)
    assert "TQQQ" not in [r["Symbol"] for r in rows]


def test_build_tws_rows_can_include_blocked_with_flag(
    tiny_watchlist: DynamicWatchlist,
) -> None:
    rows = build_tws_rows(tiny_watchlist, include_blocked=True)
    tqqq = next(r for r in rows if r["Symbol"] == "TQQQ")
    assert "BLOCKED:leveraged_etf_excluded" in tqqq["Reason"]


def test_build_tws_rows_handles_missing_metric_fields() -> None:
    sparse = DynamicWatchlist(
        date="2026-04-24",
        source="ibkr",
        symbols=[
            WatchlistCandidate(
                symbol="AAPL",
                reason=["static_core"],
                latest_price=None,
                current_dollar_volume=None,
                avg_20d_dollar_volume=None,
                relative_volume=None,
                volume_activity="unknown",
                atr_pct=None,
                realized_vol_20d=None,
                volume_rank_score=None,
            ),
        ],
    )
    rows = build_tws_rows(sparse)
    assert rows[0]["Symbol"] == "AAPL"
    # Empty strings, not "None", so CSV importers behave.
    assert rows[0]["LatestPrice"] == ""
    assert rows[0]["CurrentDollarVolume"] == ""
    assert rows[0]["RankScore"] == ""


# ---------------------------------------------------------------------------
# export_tws_watchlist end-to-end
# ---------------------------------------------------------------------------
def test_export_writes_dated_and_latest_files(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    paths = export_tws_watchlist(cfg, tiny_watchlist)

    assert paths.dated_csv.exists()
    assert paths.latest_csv.exists()
    assert paths.dated_txt.exists()
    assert paths.latest_txt.exists()
    assert paths.dated_csv.name == "2026-04-24-tws-watchlist.csv"
    assert paths.dated_txt.name == "2026-04-24-tws-symbols.txt"
    assert paths.latest_csv.name == "latest-tws-watchlist.csv"
    assert paths.latest_txt.name == "latest-tws-symbols.txt"

    # The ExportPaths metadata always asserts research-only.
    payload = paths.to_dict()
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True
    assert payload["row_count"] == 3  # blocked row excluded


def test_export_csv_columns_match_prompt(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    paths = export_tws_watchlist(cfg, tiny_watchlist)

    with paths.dated_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == list(TWS_CSV_COLUMNS)
        rows = list(reader)
    assert [r["Symbol"] for r in rows] == ["AAPL", "NVDA", "SPY"]


def test_export_txt_has_one_symbol_per_line(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    paths = export_tws_watchlist(cfg, tiny_watchlist)

    lines = paths.dated_txt.read_text(encoding="utf-8").strip().splitlines()
    assert lines == ["AAPL", "NVDA", "SPY"]
    assert lines == paths.latest_txt.read_text(encoding="utf-8").strip().splitlines()


def test_latest_files_are_rewritten_on_each_run(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    export_tws_watchlist(cfg, tiny_watchlist)

    # Second run with a different watchlist — latest-* must follow.
    second = DynamicWatchlist(
        date="2026-04-25",
        source="ibkr",
        symbols=[_make_candidate("MSFT", reason=["static_core"])],
    )
    paths = export_tws_watchlist(cfg, second)
    assert paths.latest_csv.exists()
    with paths.latest_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["Symbol"] for r in rows] == ["MSFT"]
    # Previous dated files are still there for audit.
    assert (paths.dated_csv.parent / "2026-04-24-tws-watchlist.csv").exists()


# ---------------------------------------------------------------------------
# Contract validation (pure mocks, no IBKR network)
# ---------------------------------------------------------------------------
class _FakeIB:
    def __init__(self, results: dict[str, Any] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    def qualifyContracts(self, contract: Any) -> list[Any]:
        self.calls.append(contract.symbol)
        res = self.results.get(contract.symbol)
        if res is None:
            return []
        return [res]


def test_validate_contracts_with_mock_ibkr_populates_con_id() -> None:
    fake_ib = _FakeIB(results={
        "AAPL": SimpleNamespace(conId=265598, primaryExchange="NASDAQ"),
        "ZZZZZ": None,
    })
    client = SimpleNamespace(_ib=fake_ib)
    validations = validate_contracts(client, ["AAPL", "ZZZZZ"])

    aapl = validations["AAPL"]
    assert aapl.contract_validated is True
    assert aapl.con_id == 265598
    assert aapl.primary_exchange == "NASDAQ"
    assert aapl.validation_warning == ""

    unknown = validations["ZZZZZ"]
    assert unknown.contract_validated is False
    assert unknown.con_id is None
    assert "qualifyContracts_returned_empty" in unknown.validation_warning


def test_validate_contracts_survives_exception() -> None:
    class Boom(_FakeIB):
        def qualifyContracts(self, contract: Any) -> list[Any]:
            raise RuntimeError("simulated outage")

    client = SimpleNamespace(_ib=Boom())
    validations = validate_contracts(client, ["AAPL"])
    assert validations["AAPL"].contract_validated is False
    assert "qualifyContracts_failed" in validations["AAPL"].validation_warning


def test_validate_contracts_without_connection_uses_offline_inference() -> None:
    client = SimpleNamespace(_ib=None)
    validations = validate_contracts(client, ["AAPL", "ZZZZZ"])
    aapl = validations["AAPL"]
    assert aapl.contract_validated is False
    assert aapl.primary_exchange == "NASDAQ"
    assert aapl.validation_warning == "ibkr_client_not_connected"

    unknown = validations["ZZZZZ"]
    assert unknown.primary_exchange == ""
    assert "primary_exchange_unknown" in unknown.validation_warning


def test_validate_contracts_integration_into_csv(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    fake_ib = _FakeIB(results={
        "AAPL": SimpleNamespace(conId=265598, primaryExchange="NASDAQ"),
        "NVDA": SimpleNamespace(conId=4815747, primaryExchange="NASDAQ"),
        "SPY": SimpleNamespace(conId=756733, primaryExchange="ARCA"),
    })
    client = SimpleNamespace(_ib=fake_ib)
    validations = validate_contracts(
        client, [c.symbol for c in tiny_watchlist.symbols if not c.blocked],
    )
    rows = build_tws_rows(tiny_watchlist, validations)
    by_sym = {r["Symbol"]: r for r in rows}
    assert by_sym["AAPL"]["ConId"] == 265598
    assert by_sym["AAPL"]["ContractValidated"] == "true"
    assert by_sym["AAPL"]["ValidationWarning"] == ""
    assert by_sym["SPY"]["PrimaryExchange"] == "ARCA"


# ---------------------------------------------------------------------------
# load_watchlist_by_date_or_latest
# ---------------------------------------------------------------------------
def test_load_latest_returns_most_recent_file(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    save_dynamic_watchlist(cfg, tiny_watchlist)

    wl, path = load_watchlist_by_date_or_latest(cfg, latest=True)
    assert wl is not None
    assert path is not None
    assert path.name == "2026-04-24-dynamic-watchlist.json"
    assert [c.symbol for c in wl.symbols] == [
        "AAPL", "NVDA", "SPY", "TQQQ",
    ]


def test_load_by_date_missing_returns_none(tmp_project: Path) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    wl, path = load_watchlist_by_date_or_latest(cfg, date="1999-01-01")
    assert wl is None
    assert path is None


# ---------------------------------------------------------------------------
# CLI: export-tws-watchlist
# ---------------------------------------------------------------------------
def _cli_app(tmp_project: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI's ``_bootstrap`` at the isolated tmp project root.

    We mirror the pattern used in ``tests/test_scheduler.py`` so
    ``load_config()`` inside the CLI resolves to the copied
    ``config/`` and the fresh ``data/`` underneath ``tmp_project``.
    """
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )
    return cli_module.app


def test_cli_export_latest_writes_files(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    save_dynamic_watchlist(cfg, tiny_watchlist)

    app = _cli_app(tmp_project, monkeypatch)
    result = CliRunner().invoke(
        app, ["export-tws-watchlist", "--latest"], catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_project / "data/watchlists/latest-tws-watchlist.csv").exists()
    assert (tmp_project / "data/watchlists/latest-tws-symbols.txt").exists()
    assert (
        tmp_project / "data/watchlists/2026-04-24-tws-watchlist.csv"
    ).exists()


def test_cli_export_requires_date_or_latest(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _cli_app(tmp_project, monkeypatch)
    result = CliRunner().invoke(
        app, ["export-tws-watchlist"], catch_exceptions=False,
    )
    assert result.exit_code == 2
    assert "Pass --latest or --date" in result.stdout


def test_cli_export_prints_execution_allowed_false(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    save_dynamic_watchlist(cfg, tiny_watchlist)

    app = _cli_app(tmp_project, monkeypatch)
    result = CliRunner().invoke(
        app, ["export-tws-watchlist", "--latest"], catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert '"execution_allowed": false' in result.stdout


# ---------------------------------------------------------------------------
# CLI: build-watchlist triggers the export automatically
# ---------------------------------------------------------------------------
def test_build_watchlist_auto_exports_tws_files(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build-watchlist`` must emit the TWS artefacts by default."""
    app = _cli_app(tmp_project, monkeypatch)
    import bot.cli as cli_mod

    def _fake_universe(cfg, seed, *, use_ibkr, ibkr_days):
        return (list(tiny_watchlist.symbols), None, ["no-ibkr"])

    def _fake_build(*, universe_candidates, static_core, cfg, blocked_symbols,
                    source):
        return tiny_watchlist

    monkeypatch.setattr(cli_mod, "_build_universe_candidates", _fake_universe)
    monkeypatch.setattr(cli_mod, "build_dynamic_watchlist", _fake_build)

    result = CliRunner().invoke(
        app, ["build-watchlist", "--save"], catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_project / "data/watchlists/latest-tws-watchlist.csv").exists()
    assert (tmp_project / "data/watchlists/latest-tws-symbols.txt").exists()
    assert (
        tmp_project / "data/watchlists/2026-04-24-tws-watchlist.csv"
    ).exists()


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------
def test_export_never_imports_broker_module() -> None:
    """The export module must not touch broker execution paths.

    We use AST so the docstring (which *describes* the guarantees) is
    allowed to mention the word ``bot.broker`` without tripping the
    check. The real assertion is about imports and attribute usage.
    """
    import ast
    src = Path(__file__).resolve().parents[1] / "bot" / "tws_watchlist_export.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(str(node.module or ""))
            for a in node.names:
                imports.append(f"{node.module}.{a.name}")
    assert not any("broker" in i for i in imports), imports

    # ``place_order`` / ``enable_trading`` must not appear as names or
    # attribute accesses anywhere in the AST (docstrings are excluded
    # from this check because they only *describe* the guarantee).
    banned = {"place_order", "enable_trading"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned, (
                f"forbidden attribute access: {node.attr}"
            )
        if isinstance(node, ast.Name):
            assert node.id not in banned, (
                f"forbidden name reference: {node.id}"
            )


def test_export_never_calls_broker_place_order(
    tmp_project: Path, tiny_watchlist: DynamicWatchlist,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub ``Broker.place_order`` and assert the CLI export never hits it."""
    from bot import broker as broker_mod
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    save_dynamic_watchlist(cfg, tiny_watchlist)

    place_order_calls: list[Any] = []

    def _never(*args: Any, **kwargs: Any) -> Any:
        place_order_calls.append((args, kwargs))
        raise AssertionError("export path must not call place_order")

    monkeypatch.setattr(broker_mod.Broker, "place_order", _never)

    app = _cli_app(tmp_project, monkeypatch)
    result = CliRunner().invoke(
        app, ["export-tws-watchlist", "--latest"], catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert place_order_calls == []


def test_opening_review_sequence_contains_export_step(
    tmp_project: Path,
) -> None:
    from bot.config import load_config
    from bot.daily_scheduler import load_schedule_jobs

    cfg = load_config(project_root=tmp_project)
    jobs = {j.name: j for j in load_schedule_jobs(cfg)}
    seq = jobs["opening_smc_review"].sequence
    first_tokens = [s.split()[0] for s in seq]
    assert "export-tws-watchlist" in first_tokens
    # Order matters: export must run AFTER build-watchlist and BEFORE
    # scan-smc-watchlist so the TWS files reflect today's universe.
    assert first_tokens.index("export-tws-watchlist") == (
        first_tokens.index("build-watchlist") + 1
    )
    assert first_tokens.index("export-tws-watchlist") < first_tokens.index(
        "scan-smc-watchlist"
    )


def test_opening_review_runs_export_step(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_opening_review_now`` must dispatch the export step in order."""
    from bot.config import load_config
    from bot.daily_scheduler import run_opening_review_now
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    called: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        called.append(list(argv))
        return 0

    result = run_opening_review_now(cfg, journal, command_fn=fake_runner)
    assert result["status"] == "success"
    first_tokens = [c[0] for c in called]
    assert first_tokens == [
        "market-regime", "build-watchlist", "export-tws-watchlist",
        "scan-smc-watchlist", "smc-review-queue",
    ]
    # Ensure export ran with --latest and --telegram.
    export_argv = next(c for c in called if c[0] == "export-tws-watchlist")
    assert "--latest" in export_argv
    assert "--telegram" in export_argv
    assert result["execution_allowed"] is False
