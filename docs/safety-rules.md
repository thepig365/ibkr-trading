# Safety rules

These invariants are enforced by the codebase. Any change that
weakens them requires an explicit code review and a corresponding
test update.

## Hard blocks (cannot be bypassed at runtime)

1. **Live trading is blocked.** While `account.block_live_trading` is
   `true` in `config/settings.yaml`, `IBKRClient.connect()` refuses to
   open a session if either:
   - `settings.account.mode != "paper"`, or
   - `IBKR_ACCOUNT_MODE != "paper"`, or
   - `IBKR_PORT` is not in the documented paper port set
     (`{7497, 4002}`).
2. **Order placement is disabled.** While `trading.enabled` is `false`,
   `Broker.place_order` raises `TradingDisabled` before reaching any
   IB write API.
3. **Disallowed asset classes / directions.** Options, crypto, forex,
   and shorting are all rejected by the risk engine while the matching
   `allow_*` flag is `false`. The defaults are all `false`.
4. **Manual confirmation.** Even with `trading.enabled=true`, every
   call to `place_order` requires `confirmed=True`. Otherwise the call
   raises `ManualConfirmationRequired`.
5. **Dry-run default.** `trading.dry_run_default` is `true`; orders
   never reach `_submit_order` unless the caller explicitly disables
   dry-run.
6. **Reconciliation gate.** When the latest reconciliation report is
   `FAIL`, the risk engine refuses new trades on the next bar.

## Soft rules (must be reflected in code review)

- Never add a write method to `bot/ibkr_client.py`. The IBKR client is
  read-only by contract.
- Never call `ib.placeOrder()` outside `bot/broker.py::_submit_order`.
- Reconciliation is read-only. It must not call any broker write API,
  even indirectly.
- Telegram credential failure must NEVER crash the bot; it must fall
  back to `memory/DAILY-SUMMARY.md`.

## MTF paper bracket (opt-in, Prompt 10C)

- **Paper only:** `account.mode` must be `paper` and
  `block_live_trading` must remain as configured. Long-only brackets;
  `broker._submit_mtf_paper_bracket` is the only code path that calls
  `ib.placeOrder` (see `bot/broker.py`).
- **Config gates:** `trading.enabled`, `trading.mtf_paper_bracket_enabled`,
  and (for real TWS submission) `trading.mtf_paper_dry_run: false`. CLI
  flag `--paper-bracket` is ignored if `mtf_paper_bracket_enabled` is
  false.
- **Preconditions:** :func:`bot.mtf_paper_execution.mtf_paper_may_run` requires
  `FULL_ALIGNMENT` and `eligible_for_future_paper_trade` on the scan
  report. IB is connected with `readonly=False` only for the submission
  step (`IBKRClient.connect(readonly=False)`).
- The generic `_submit_order` path without `mtf_paper_bracket=True` still
  raises `TradingDisabled`.

## What is intentionally NOT in this milestone

- No non-MTF / generic market order entry from strategies.
- No live-account order routing.
- No automatic 24/7 loop unless the operator schedules the CLI/scheduler
  and accepts paper risk.

When you add any of the above, update this file in the same PR.
