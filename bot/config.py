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
    root = project_root or PROJECT_ROOT
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
