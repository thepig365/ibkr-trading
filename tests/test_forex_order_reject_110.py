"""Post-submit brokerage classification stubs (110 / 135)."""

from __future__ import annotations

from types import SimpleNamespace

from bot.forex import paper_submit as ps


def _log(ec: int, msg: str = "") -> SimpleNamespace:
    return SimpleNamespace(errorCode=ec, message=msg)


class _Trade:
    def __init__(
        self, *, oid: int, statuses: tuple[str, ...], errs: tuple[SimpleNamespace, ...]
    ) -> None:
        self.order = SimpleNamespace(orderId=oid)
        self.orderStatus = SimpleNamespace(status=statuses[0], filled=0.0)
        self.log = list(errs)


def test_error_110_maps_rejected_invalid_tick() -> None:
    stub_ib = SimpleNamespace(
        openTrades=lambda: [
            _Trade(oid=1, statuses=("Cancelled",), errs=(_log(110, "min variation"),)),
        ]
    )
    out = ps._classify_orders_post_submit(stub_ib, [1])
    assert out["broker_acceptance_status"] == "broker_rejected"
    assert out["rejection_class"] == "rejected_invalid_tick"
    assert 110 in out["error_codes_seen"]


def test_135_follows_135_secondary_annotation() -> None:
    stub_ib = SimpleNamespace(
        openTrades=lambda: [
            _Trade(
                oid=2,
                statuses=("Cancelled",),
                errs=(_log(110), _log(135, "parent")),
            ),
        ]
    )
    out = ps._classify_orders_post_submit(stub_ib, [2])
    assert out["secondary_reject_note"] == "secondary_parent_missing_after_child_reject"
