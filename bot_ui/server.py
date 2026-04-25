"""Uvicorn entry point for the local Strategy Lab UI.

Run via::

    python -m bot_ui                 # uses defaults: 127.0.0.1:8765
    python -m bot_ui --port 9001
    STRATEGY_LAB_PORT=9100 python -m bot_ui

Hard rules:

* The host is always ``127.0.0.1`` unless the operator explicitly passes
  ``--host``. We never default to ``0.0.0.0``.
* No IBKR / TWS connection is made by importing :mod:`bot_ui.app`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import DEFAULT_HOST, DEFAULT_PORT, create_app

logger = logging.getLogger("bot_ui.server")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bot_ui",
        description="Local Strategy Lab UI (FastAPI). Paper-only; never connects to TWS at startup.",
    )
    p.add_argument("--host", default=os.environ.get("STRATEGY_LAB_HOST", DEFAULT_HOST))
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STRATEGY_LAB_PORT", str(DEFAULT_PORT))),
    )
    p.add_argument("--reload", action="store_true", help="Auto-reload on file changes (dev).")
    p.add_argument(
        "--log-level",
        default=os.environ.get("STRATEGY_LAB_LOG_LEVEL", "info"),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "Host %r is not loopback. The Strategy Lab UI is intended for "
            "local-only use. Continuing anyway because you asked.",
            args.host,
        )

    # Defer import to keep --help fast and to make sure no TWS code runs
    # if the user only wanted to print help / version.
    import uvicorn

    os.environ.setdefault("STRATEGY_LAB_HOST", args.host)
    os.environ.setdefault("STRATEGY_LAB_PORT", str(args.port))

    app = create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry
    sys.exit(main())
