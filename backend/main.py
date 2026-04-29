"""FastAPI application entry point for the IBKR Trading Engine.

Startup loads configuration, initializes SQLite, connects to IBKR Paper Gateway,
starts WebSocket status broadcasting, and subscribes to configured 1-minute
bars. The trading engine, strategy, risk manager, trade manager, news feed,
and Telegram bot are all wired here.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import routes, websocket
from backend.api.websocket import EngineStatusBroadcaster
from backend.config import AppConfig, load_config, resolve_project_path
from backend.connection.connection_manager import ConnectionManager, ConnectionState
from backend.data.ibkr_data import IBKRDataFeed
from backend.db.database import Database
from backend.engine.trading_engine import TradingEngine
from backend.execution.risk_manager import RiskManager
from backend.execution.trade_manager import TradeManager
from backend.notifications.finnhub_feed import FinnhubFeed
from backend.notifications.telegram_bot import TelegramBot
from backend.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


def configure_logging(config: AppConfig) -> None:
    """Configure console and file logging."""

    log_file = resolve_project_path(config.logging.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config.logging.level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
        force=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""

    config = load_config()
    configure_logging(config)
    logger.info("Starting IBKR Trading Engine (strategy=%s)", config.strategy)

    database = Database(config.db.path)
    await database.initialize()

    status_broadcaster = EngineStatusBroadcaster()
    connection_manager = ConnectionManager(
        config,
        status_broadcast=status_broadcaster.broadcast,
    )
    data_feed = IBKRDataFeed(connection_manager.ib, database)
    strategy = StrategyRegistry.load(config)
    risk_manager = RiskManager(config, database)
    trade_manager = TradeManager(
        config,
        database,
        connection_manager.ib,
        risk=risk_manager,
    )
    news_feed = FinnhubFeed(config)
    telegram_bot = TelegramBot(config)
    trading_engine = TradingEngine(
        config=config,
        database=database,
        connection_manager=connection_manager,
        strategy=strategy,
        risk_manager=risk_manager,
        trade_manager=trade_manager,
        news_feed=news_feed,
        telegram_bot=telegram_bot,
    )
    news_feed.bind_position_resolver(lambda: trading_engine.news_open_symbols())
    news_feed.set_high_impact_handler(trading_engine.deliver_high_impact_news_alerts)

    data_feed.set_on_bar(trading_engine.on_bar)
    telegram_bot.bind_engine(trading_engine)
    connection_manager._error_notifier = telegram_bot.send_error  # type: ignore[attr-defined]

    app.state.config = config
    app.state.database = database
    app.state.status_broadcaster = status_broadcaster
    app.state.connection_manager = connection_manager
    app.state.data_feed = data_feed
    app.state.strategy = strategy
    app.state.risk_manager = risk_manager
    app.state.trade_manager = trade_manager
    app.state.news_feed = news_feed
    app.state.telegram_bot = telegram_bot
    app.state.engine = trading_engine
    app.state.last_account_snapshot_monotonic = 0.0

    await connection_manager.connect()
    if connection_manager.state == ConnectionState.CONNECTED:
        try:
            await data_feed.subscribe_symbols(config.symbols)
        except Exception:  # noqa: BLE001
            logger.exception("IBKR bar subscription failed")
    else:
        logger.warning(
            "IBKR startup connection unavailable; dashboard remains active in %s state",
            connection_manager.state.value,
        )

    await news_feed.start(config.symbols)
    await telegram_bot.start()
    await trading_engine.start()

    status_task = asyncio.create_task(
        status_broadcaster.run_periodic(connection_manager.status_dict),
        name="engine-status-broadcast",
    )
    eod_task = asyncio.create_task(
        trading_engine.run_eod_loop(), name="engine-eod-loop"
    )
    app.state.status_task = status_task
    app.state.eod_task = eod_task

    try:
        yield
    finally:
        logger.info("Shutting down IBKR Trading Engine")
        for task in (status_task, eod_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await trading_engine.stop()
        await news_feed.stop()
        await telegram_bot.stop()
        await data_feed.unsubscribe_all()
        await connection_manager.disconnect(reason="shutdown")
        await database.close()


app = FastAPI(title="IBKR Trading Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(routes.router)
app.include_router(websocket.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint for quick service identification."""

    return {"service": "IBKR Trading Engine", "status": "running"}
