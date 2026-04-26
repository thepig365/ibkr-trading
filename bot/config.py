"""Configuration loader.

Loads `config/settings.yaml`, `config/strategy.yaml`, `config/watchlist.yaml`
and environment variables (via python-dotenv). All callers should obtain
configuration through `load_config()` rather than reading files directly.

Pydantic models enforce that safety-relevant fields exist and have the
expected types. Anything dangerous defaults to OFF.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class AccountConfig(BaseModel):
    mode: str = "paper"
    block_live_trading: bool = True

    @field_validator("mode")
    @classmethod
    def _normalize_mode(cls, v: str) -> str:
        return v.strip().lower()


class MtfAutoPaperConfig(BaseModel):
    """10H/Full prompt: auto paper MTF loop settings (PAPER only; never live)."""

    enabled: bool = False
    account_mode: str = "paper"
    allow_live_trading: bool = False
    fully_automatic: bool = False

    model_config = {"extra": "ignore"}


class IntradayPaperConfig(BaseModel):
    """13F: ICT/SMC intraday paper bracket execution (PAPER only; never live).

    Hard invariants enforced by :mod:`bot.execution.intraday_paper_execution`
    and :class:`bot.broker.Broker._submit_intraday_paper_bracket`:

    * paper_only / live_trading_allowed / market_orders_allowed must remain
      ``True / False / False``. The loader rejects any deviation.
    * bracket / stop / target are always required at submit time. There is
      no flag a user can set that turns this into a single LIMIT or MKT.
    """

    enabled: bool = False
    fully_automatic: bool = False
    allow_strict_entries: bool = True
    allow_aggressive_entries: bool = True
    risk_per_trade_pct: float = 0.10
    max_concurrent_positions: int = 5
    max_one_position_per_symbol: bool = True
    require_reconciliation_pass: bool = True
    no_new_entries_before: str = "09:45"
    no_new_entries_after: str = "15:30"
    exit_open_positions_at: str = "15:55"
    paper_only: bool = True
    live_trading_allowed: bool = False
    market_orders_allowed: bool = False
    bracket_required: bool = True
    stop_required: bool = True
    target_required: bool = True
    # Defence-in-depth knobs the operator should not normally touch.
    # ``dry_run`` mirrors ``trading.mtf_paper_dry_run``: when ``True``
    # the broker validates the bracket but does not call placeOrder.
    dry_run: bool = True
    min_rr: float = 1.2
    # Prompt 13K.3: explicit TIF for all bracket legs; paper notional / qty caps.
    tif: str = "DAY"
    max_notional_per_order_usd: float = 10_000.0
    max_daily_notional_usd: float = 100_000.0
    max_equity_per_position_pct: float = 10.0
    max_quantity_per_order: int = 100
    # Prompt 13L-alt: ticker edge profiles gate paper size / mode.
    edge_profile_enabled: bool = True
    unknown_edge_policy: str = "allow_strict_small_risk"  # allow_strict_small_risk|watch_only|block_all
    unknown_edge_risk_multiplier: float = 0.25
    allow_aggressive_without_edge_profile: bool = False

    model_config = {"extra": "ignore"}

    @field_validator("tif")
    @classmethod
    def _tif_paper_bracket_only(cls, v: str) -> str:
        s = (v or "DAY").strip().upper()
        if s not in {"DAY"}:
            raise ValueError("trading.intraday_paper.tif must be 'DAY' (Prompt 13K.3).")
        return s

    @field_validator("max_notional_per_order_usd", "max_daily_notional_usd")
    @classmethod
    def _paper_usd_cap_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("paper USD notional caps must be > 0")
        return v

    @field_validator("max_equity_per_position_pct")
    @classmethod
    def _paper_eq_pct_sane(cls, v: float) -> float:
        if v <= 0 or v > 100.0:
            raise ValueError("trading.intraday_paper.max_equity_per_position_pct must be in (0, 100].")
        return v

    @field_validator("unknown_edge_policy")
    @classmethod
    def _edge_policy_ok(cls, v: str) -> str:
        s = (v or "allow_strict_small_risk").strip()
        if s not in {"allow_strict_small_risk", "watch_only", "block_all"}:
            raise ValueError("unknown_edge_policy must be allow_strict_small_risk|watch_only|block_all")
        return s

    @field_validator("unknown_edge_risk_multiplier")
    @classmethod
    def _unk_edge_mult(cls, v: float) -> float:
        if v < 0 or v > 1.0:
            raise ValueError("unknown_edge_risk_multiplier must be in [0, 1]")
        return v

    @field_validator("max_quantity_per_order")
    @classmethod
    def _max_q_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("trading.intraday_paper.max_quantity_per_order must be >= 1")
        return v

    @field_validator("paper_only")
    @classmethod
    def _paper_only_must_be_true(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError(
                "trading.intraday_paper.paper_only must be true (paper-only invariant)."
            )
        return True

    @field_validator("live_trading_allowed")
    @classmethod
    def _live_trading_must_be_false(cls, v: bool) -> bool:
        if v is not False:
            raise ValueError(
                "trading.intraday_paper.live_trading_allowed must be false."
            )
        return False

    @field_validator("market_orders_allowed")
    @classmethod
    def _market_orders_must_be_false(cls, v: bool) -> bool:
        if v is not False:
            raise ValueError(
                "trading.intraday_paper.market_orders_allowed must be false."
            )
        return False

    @field_validator("bracket_required", "stop_required", "target_required")
    @classmethod
    def _bracket_pieces_required(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError(
                "trading.intraday_paper.{bracket,stop,target}_required must all be true."
            )
        return True

    @field_validator("risk_per_trade_pct", "min_rr")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("max_concurrent_positions")
    @classmethod
    def _non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v


class TradingConfig(BaseModel):
    enabled: bool = False
    dry_run_default: bool = True
    require_manual_confirmation: bool = True
    allow_options: bool = False
    allow_crypto: bool = False
    allow_forex: bool = False
    allow_shorting: bool = False
    # MTF paper bracket (Prompt 10C): only when mtf_paper_bracket_enabled and
    # alignment is FULL_ALIGNMENT; still paper account + block_live_trading.
    mtf_paper_bracket_enabled: bool = False
    mtf_paper_bypass_manual_confirmation: bool = True
    mtf_paper_dry_run: bool = False
    mtf_paper_require_full_alignment: bool = True
    # 10G: explicit 5m trigger + optional auto from trigger-check/watch
    mtf_paper_require_confirmed_5m: bool = True
    mtf_paper_auto_bracket_enabled: bool = False
    mtf_auto_paper: MtfAutoPaperConfig = Field(default_factory=MtfAutoPaperConfig)
    # 13F: intraday paper bracket execution (PAPER only; never live).
    intraday_paper: IntradayPaperConfig = Field(default_factory=IntradayPaperConfig)


class RiskConfig(BaseModel):
    max_account_risk_per_trade_pct: float = 1.0
    max_equity_per_position_pct: float = 10.0
    max_open_positions: int = 5
    block_new_trades_if_reconciliation_fails: bool = True


class TelegramConfig(BaseModel):
    enabled: bool = True
    privacy_mode: bool = True
    parse_mode: str = "HTML"

    @field_validator("parse_mode")
    @classmethod
    def _normalize_parse_mode(cls, v: str) -> str:
        v = (v or "").strip()
        allowed = {"HTML", "Markdown", "MarkdownV2", ""}
        if v not in allowed:
            raise ValueError(
                f"parse_mode must be one of {sorted(allowed)!r}, got {v!r}"
            )
        return v


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class PathsConfig(BaseModel):
    data_dir: str = "data"
    memory_dir: str = "memory"
    sqlite_file: str = "data/trading_bot.sqlite"
    orders_jsonl: str = "data/orders.jsonl"
    executions_jsonl: str = "data/executions.jsonl"
    account_snapshots_jsonl: str = "data/account_snapshots.jsonl"
    daily_summary_md: str = "memory/DAILY-SUMMARY.md"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class TimeframeConfig(BaseModel):
    """One entry under ``settings.smc_timeframes``.

    All fields are optional so the default registry in
    :mod:`bot.smc_timeframes` stays authoritative. Operators only set
    the keys they want to override.
    """

    enabled: bool | None = True
    duration: str | None = None
    bar_size: str | None = None
    use_rth: bool | None = None
    min_bars: int | None = None
    max_bars: int | None = None
    what_to_show: str | None = None
    role: str | None = None  # e.g. structure_confirmation, entry_trigger (MTF / docs)

    model_config = {"extra": "ignore"}


class SmcTimeframesConfig(BaseModel):
    """``settings.smc_timeframes`` container.

    Use populate-by-name so both ``30min`` (the canonical label) and
    ``m30`` (a pydantic-friendly alias) round-trip cleanly.
    """

    daily: TimeframeConfig | None = None
    h4: TimeframeConfig | None = Field(default=None, alias="4h")
    m30: TimeframeConfig | None = Field(default=None, alias="30min")
    m5: TimeframeConfig | None = Field(default=None, alias="5min")
    # Prompt 13D — 1min trigger timeframe for ICT/SMC Intraday V1.
    m1: TimeframeConfig | None = Field(default=None, alias="1min")

    model_config = {"populate_by_name": True, "extra": "allow"}

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        """Always dump with canonical ``4h`` / ``30min`` / ``5min`` keys."""
        data = super().model_dump(by_alias=True, **kwargs)
        if "4h" not in data and data.get("h4") is not None:
            data["4h"] = data.pop("h4")
        data.pop("h4", None)
        # Back-fill from the alias-free attribute when only ``m30`` was set.
        if "30min" not in data and data.get("m30") is not None:
            data["30min"] = data.pop("m30")
        data.pop("m30", None)
        if "5min" not in data and data.get("m5") is not None:
            data["5min"] = data.pop("m5")
        data.pop("m5", None)
        if "1min" not in data and data.get("m1") is not None:
            data["1min"] = data.pop("m1")
        data.pop("m1", None)
        return data


class MarketRegimeConfig(BaseModel):
    """Knobs for :func:`bot.market_regime.evaluate_regime`.

    These flags tighten or loosen the confidence floor but they
    cannot enable execution globally - that remains off until the
    broker layer is re-wired. See docs/market-regime.md.
    """

    allow_medium_confidence_for_research: bool = True
    allow_medium_confidence_for_new_positions: bool = False
    require_vix_for_execution: bool = True
    require_spy_200ma_for_execution: bool = True
    require_qqq_200ma_for_execution: bool = False


class IBKREnv(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    account_mode: str = "paper"

    @field_validator("account_mode")
    @classmethod
    def _normalize_account_mode(cls, v: str) -> str:
        return v.strip().lower()


class TelegramEnv(BaseModel):
    bot_token: str | None = None
    chat_id: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id)


class PerplexityEnv(BaseModel):
    api_key: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


class StrategyConfig(BaseModel):
    name: str = "none"
    enabled: bool = False
    description: str = ""


class PremarketBriefConfig(BaseModel):
    """Pre-market human brief (read-only; never triggers trades)."""

    target_ny_time: str = "08:30"
    timezone: str = "America/New_York"


class ReportsConfig(BaseModel):
    """Report delivery and local retention (Strategy Lab; never weakens live-trading safety)."""

    email_to: str = "ileonzh@gmail.com"
    email_enabled: bool = True
    persistence_mode: str = "ephemeral"  # ephemeral | keep_local
    keep_local_reports_hours: int = 24
    keep_charts_days: int = 7

    @field_validator("persistence_mode")
    @classmethod
    def _mode_ok(cls, v: str) -> str:
        s = (v or "ephemeral").strip().lower()
        if s not in {"ephemeral", "keep_local"}:
            raise ValueError("reports.persistence_mode must be ephemeral|keep_local")
        return s

    model_config = {"extra": "ignore"}


class Settings(BaseModel):
    account: AccountConfig = Field(default_factory=AccountConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    market_regime: MarketRegimeConfig = Field(default_factory=MarketRegimeConfig)
    # Per-timeframe IBKR candle presets for SMC/ICT scanning (Prompt 10A).
    # Research-only; these control how candles are fetched, never
    # whether the bot can trade.
    smc_timeframes: SmcTimeframesConfig = Field(default_factory=SmcTimeframesConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    premarket_brief: PremarketBriefConfig = Field(default_factory=PremarketBriefConfig)


class AppConfig(BaseModel):
    """Top-level configuration object passed around the bot."""

    settings: Settings
    strategy: StrategyConfig
    # Raw `strategies:` mapping from config/strategy.yaml. Each entry
    # is a per-strategy block (e.g. ``SMC_LIQUIDITY_REVERSAL_RESEARCH``)
    # that downstream modules read directly. The legacy ``strategy``
    # field above is the placeholder kept for backwards compatibility.
    strategies: dict[str, Any] = Field(default_factory=dict)
    # Raw ``review_queue:`` block from config/strategy.yaml. Read by
    # :mod:`bot.review_queue` and the ``smc-review-queue`` CLI. Kept
    # loose-typed so the queue can evolve without a schema migration.
    review_queue: dict[str, Any] = Field(default_factory=dict)
    # Raw ``schedule.yaml`` block. Consumed by :mod:`bot.daily_scheduler`.
    schedule: dict[str, Any] = Field(default_factory=dict)
    # Raw ``telegram.yaml`` block. Consumed by
    # :mod:`bot.telegram_commands` for command polling / authorization.
    # Kept loose-typed so the command interface can evolve without
    # a schema migration.
    telegram_cfg: dict[str, Any] = Field(default_factory=dict)
    watchlist: dict[str, Any]
    news: dict[str, Any]
    ibkr: IBKREnv
    telegram: TelegramEnv
    perplexity: PerplexityEnv
    project_root: Path

    model_config = {"arbitrary_types_allowed": True}

    def absolute(self, relative: str) -> Path:
        """Resolve a path that may be relative to the project root."""
        p = Path(relative)
        return p if p.is_absolute() else (self.project_root / p)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a deep copy of ``base`` (YAML settings)."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _load_env(project_root: Path) -> tuple[IBKREnv, TelegramEnv, PerplexityEnv]:
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    ibkr = IBKREnv(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", "7497")),
        client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
        account_mode=os.getenv("IBKR_ACCOUNT_MODE", "paper"),
    )
    telegram = TelegramEnv(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )
    perplexity = PerplexityEnv(api_key=os.getenv("PERPLEXITY_API_KEY") or None)
    return ibkr, telegram, perplexity


def _project_root_from_environment() -> Path | None:
    """Optional hermetic project root for subprocess/CLI tests.

    When set, all ``data/`` and ``config/`` resolution follows this
    directory instead of the install location. Recognised (first wins)::

    * ``IBKR_TRADING_PROJECT_ROOT`` — preferred, explicit
    * ``BOT_PROJECT_ROOT`` — legacy alias (see e.g. chart CLI tests)
    """
    for key in ("IBKR_TRADING_PROJECT_ROOT", "BOT_PROJECT_ROOT"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
    return None


def load_config(
    project_root: Path | None = None,
    settings_path: Path | None = None,
    strategy_path: Path | None = None,
    watchlist_path: Path | None = None,
    news_path: Path | None = None,
    schedule_path: Path | None = None,
    telegram_path: Path | None = None,
) -> AppConfig:
    """Load and validate configuration from disk and environment.

    Parameters are exposed mainly for tests; production code calls
    `load_config()` with no arguments.
    """
    root = project_root or _project_root_from_environment() or PROJECT_ROOT
    s_path = settings_path or (root / "config" / "settings.yaml")
    s_local = root / "config" / "settings.local.yaml"
    st_path = strategy_path or (root / "config" / "strategy.yaml")
    w_path = watchlist_path or (root / "config" / "watchlist.yaml")
    n_path = news_path or (root / "config" / "news.yaml")

    settings_raw = _read_yaml(s_path)
    if (
        s_local.is_file()
        and not settings_path
        and not os.environ.get("PYTEST_VERSION")
    ):
        # Local-only overlay (gitignored). Tests run under pytest (PYTEST_VERSION
        # set) so the repo can keep settings.local.yaml without changing tmp fixtures.
        settings_raw = _deep_merge_dict(settings_raw, _read_yaml(s_local))
    strategy_yaml = _read_yaml(st_path)
    strategy_raw = strategy_yaml.get("strategy", {})
    strategies_raw = strategy_yaml.get("strategies", {}) or {}
    review_queue_raw = strategy_yaml.get("review_queue", {}) or {}
    watchlist_raw = _read_yaml(w_path)
    news_raw = _read_yaml(n_path)
    sch_path = schedule_path or (root / "config" / "schedule.yaml")
    schedule_yaml = _read_yaml(sch_path)
    schedule_raw = schedule_yaml.get("schedule", {}) or {}

    tel_path = telegram_path or (root / "config" / "telegram.yaml")
    telegram_yaml = _read_yaml(tel_path)
    telegram_raw = telegram_yaml.get("telegram", {}) or {}

    settings = Settings(**settings_raw)
    strategy = StrategyConfig(**strategy_raw)

    ibkr, telegram, perplexity = _load_env(root)

    return AppConfig(
        settings=settings,
        strategy=strategy,
        strategies=strategies_raw,
        review_queue=review_queue_raw,
        schedule=schedule_raw,
        telegram_cfg=telegram_raw,
        watchlist=watchlist_raw,
        news=news_raw,
        ibkr=ibkr,
        telegram=telegram,
        perplexity=perplexity,
        project_root=root,
    )
