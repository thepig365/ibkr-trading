"""Local Strategy Lab UI (FastAPI).

This package is the *thin* read-and-enqueue UI layer. It is deliberately
isolated from `bot.broker` and `bot.ibkr_client` so that:

    1. Importing the UI never opens a TWS connection.
    2. The same UI code can later run on Vercel by swapping the local
       `state_store` / `command_queue` backends for database-backed ones.

The UI is paper-only by construction; it cannot place orders.
"""

from __future__ import annotations

__all__ = ["create_app"]

# Re-exported lazily inside app.py to keep import-time side effects tiny.
def create_app(*args, **kwargs):  # pragma: no cover - thin re-export
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
