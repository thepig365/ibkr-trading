"""Stable IBKR `client_id` ranges for subprocess / CLI tooling.

Interactive Brokers permits one API login per `(host, port, clientId)`.
Operational defaults often set ``IBKR_CLIENT_ID=1``. Long-running supervisors
and one-off CLI read-only scans must not fight for that id.

Reserve ranges (recommended):
* **1–9** — primary engine / human default (env-driven ``IBKR_CLIENT_ID``, often ``1``)
* **2** — broker read-only snapshots (portfolio / orders / reconcile) when routed here
* **20–39** — read-only batch research / caches (watchlist build, scans, candles)
* **40+** — discretionary / future hooks

Trading / bracket paths intentionally keep using :class:`bot.config.IBKREnv`
as loaded from the environment so paper engines stay behaviour-compatible.
CLI read-only tooling uses overrides from :mod:`bot.ibkr_connection`.
"""

from __future__ import annotations

# Mirrors common default IBKR_CLIENT_ID=1 — document only; callers use cfg.
PAPER_ENGINE_DEFAULT = 1

# Portfolio, open orders, reconcile, session status-style broker snapshots.
BROKER_READ_ONLY = 2

# One-off CLI: ``build-watchlist --ibkr`` pulls daily bars.
WATCHLIST_FETCH = 20

# ``fetch-candles --ibkr``, MTF/scan candle loads sharing the candle path.
CANDLE_FETCH = 21

RESEARCH_FETCH = 22
EDGE_FETCH = 23

# Unused reserved block (UI batch jobs, dashboards, future).
UI_COMMAND_BASE = 30

__all__ = [
    "PAPER_ENGINE_DEFAULT",
    "BROKER_READ_ONLY",
    "WATCHLIST_FETCH",
    "CANDLE_FETCH",
    "RESEARCH_FETCH",
    "EDGE_FETCH",
    "UI_COMMAND_BASE",
]
