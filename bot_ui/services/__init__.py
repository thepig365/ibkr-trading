"""Service abstractions used by the local Strategy Lab UI.

Two abstractions live here so that the same UI code can run locally
today and on Vercel + Worker tomorrow:

* :mod:`state_store` reads operational state (watchlist, signals,
  account, loop heartbeat). Local backend reads files; remote backend
  will read Postgres.
* :mod:`command_queue` runs / enqueues *allowlisted* CLI commands.
  Local backend shells out to ``python -m bot.cli ...``. Remote backend
  will INSERT into a ``commands`` table consumed by the worker.

Neither service may import :mod:`bot.broker` or :mod:`bot.ibkr_client`.
The UI must never open a TWS connection at startup.
"""
