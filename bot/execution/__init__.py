"""Execution facades (Prompt 13F).

This package wraps the broker for **paper-only** execution paths used by the
forward-testing CLIs and the UI command runner. It MUST NOT introduce a live
trading code path or a market-order code path; both are blocked at the
:mod:`bot.broker` layer and re-validated here.

Submodules:

* :mod:`bot.execution.intraday_paper_execution` — ICT/SMC intraday paper
  bracket: build / validate / submit / batch-pass / Chinese digest.
"""

from .intraday_paper_execution import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    INTRADAY_LOOP_STATE_RELPATH,
    KILL_SWITCH_RELPATH,
    PAPER_ORDERS_DIR,
    IntradayPaperIntent,
    IntradayPaperPassResult,
    IntradayPaperSubmissionResult,
    build_intraday_paper_intent,
    format_intraday_paper_digest_zh,
    is_intraday_paper_runtime_enabled,
    is_kill_switch_active,
    run_intraday_paper_pass,
    submit_intraday_paper_bracket,
    validate_intraday_paper_intent,
)

__all__ = [
    "INTRADAY_AUTO_PAPER_ENABLED_RELPATH",
    "INTRADAY_LOOP_STATE_RELPATH",
    "KILL_SWITCH_RELPATH",
    "PAPER_ORDERS_DIR",
    "IntradayPaperIntent",
    "IntradayPaperPassResult",
    "IntradayPaperSubmissionResult",
    "build_intraday_paper_intent",
    "format_intraday_paper_digest_zh",
    "is_intraday_paper_runtime_enabled",
    "is_kill_switch_active",
    "run_intraday_paper_pass",
    "submit_intraday_paper_bracket",
    "validate_intraday_paper_intent",
]
