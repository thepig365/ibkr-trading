"""Configuration loading for the IBKR Trading Engine.

This module reads config.yaml, interpolates environment variables in ${VAR}
format, and validates the result with Pydantic models. Secrets are expected to
come from .env or the shell environment, never from hardcoded source code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class IBKRConfig(BaseModel):
    """Interactive Brokers connection settings."""

    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    account: str = ""
    allow_live_trading: bool = False


class ConnectionConfig(BaseModel):
    """Connection lifecycle and heartbeat settings."""

    auto_disconnect_minutes: int = 30
    heartbeat_interval_sec: int = 30
    reconnect_timeout_sec: int = 10


class RiskConfig(BaseModel):
    """Risk control settings."""

    max_risk_per_trade: float = 0.01
    max_daily_loss: float = 0.02
    max_trades_per_day: int = 2
    min_rr_ratio: float = 2.0
    max_sl_width_pct: float = 0.015
    daily_capital_limit: float = 100000.0


class ICTConfig(BaseModel):
    """ICT strategy settings."""

    min_fvg_size: float = 0.05
    auto_threshold: int = 60
    alert_threshold: int = 40
    trailing_activation_r: float = 1.0
    trailing_distance_r: float = 0.5
    max_scale_ins: int = 2
    scale_in_threshold_r: float = 1.0


class FinnhubConfig(BaseModel):
    """Finnhub market news configuration."""

    api_key: str = ""
    watchlist: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    """Telegram notification configuration."""

    bot_token: str = ""
    chat_id: str = ""


class DBConfig(BaseModel):
    """Database configuration."""

    type: str = "sqlite"
    path: str = "./data/trades.db"


class ServerConfig(BaseModel):
    """FastAPI server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000


class LoggingConfig(BaseModel):
    """Application logging configuration."""

    level: str = "INFO"
    file: str = "./logs/engine.log"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    ibkr: IBKRConfig
    connection: ConnectionConfig
    strategy: str = "ICT"
    symbols: list[str] = Field(default_factory=list)
    risk: RiskConfig
    ict: ICTConfig
    finnhub: FinnhubConfig
    telegram: TelegramConfig
    db: DBConfig
    server: ServerConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders using environment variables."""

    if isinstance(value, dict):
        return {key: _interpolate_env(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)
    return value


def resolve_project_path(path_value: str | Path) -> Path:
    """Resolve relative config paths from the project root."""

    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load and validate application configuration.

    Args:
        config_path: Optional path to config.yaml. Relative paths are resolved
            from the project root.

    Returns:
        A validated AppConfig instance.
    """

    load_dotenv(PROJECT_ROOT / ".env")

    selected_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not selected_path.is_absolute():
        selected_path = PROJECT_ROOT / selected_path

    with selected_path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file) or {}

    interpolated = _interpolate_env(raw_config)
    return AppConfig.model_validate(interpolated)
