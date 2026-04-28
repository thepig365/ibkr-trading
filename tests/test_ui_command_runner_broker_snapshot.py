"""Allowlist wiring for broker-snapshot-refresh."""

from __future__ import annotations

import pytest

from bot_ui.services.command_queue import CommandRequest, validate_request
from bot_ui.services.safety import ALLOWED_COMMANDS, is_allowed, validate_args_for


@pytest.mark.parametrize("cmd", ["broker-snapshot-refresh", "broker-refresh"])
def test_broker_refresh_on_allowlist(cmd: str) -> None:
    assert cmd in ALLOWED_COMMANDS
    assert is_allowed(cmd) is True


@pytest.mark.parametrize(
    ("args", "accepted"),
    [
        ((), True),
        (("--json",), True),
        (("--json", "--force"), False),
        (("--live",), False),
        (("--symbol", "AAPL"), False),
    ],
)
def test_broker_refresh_args_validation(args: tuple[str, ...], accepted: bool) -> None:
    ok, msg = validate_args_for("broker-snapshot-refresh", args)
    assert ok is accepted
    ack, why = validate_request(CommandRequest(command="broker-snapshot-refresh", args=args))
    assert ack is accepted


def test_live_and_place_orders_not_added_for_refresh() -> None:
    ack, msg = validate_request(
        CommandRequest(
            command="broker-snapshot-refresh",
            args=("--dry-run", "place-order"),
        )
    )
    assert ack is False
    assert msg
