"""IBKR FX contract mappings (CASH / IDEALPRO)."""

from __future__ import annotations

from bot.forex.instrument import Instrument
from bot.forex.pairs import ibkr_contract_args, parse_pair


def test_audusd_cash_idealpro_contract() -> None:
    spec = parse_pair("AUD/USD")
    c = ibkr_contract_args(spec)
    assert c == {
        "symbol": "AUD",
        "sec_type": "CASH",
        "currency": "USD",
        "exchange": "IDEALPRO",
    }


def test_usdjpy_cash_idealpro_contract() -> None:
    spec = parse_pair("USD/JPY")
    c = ibkr_contract_args(spec)
    assert c["symbol"] == "USD"
    assert c["currency"] == "JPY"
    assert c["exchange"] == "IDEALPRO"


def test_audjpy_via_instrument_wrapper() -> None:
    ix = Instrument.from_pair_display("AUD/JPY")
    assert ix.ib_symbol == "AUD"
    assert ix.ib_currency == "JPY"
    assert ix.sec_type == "CASH"
