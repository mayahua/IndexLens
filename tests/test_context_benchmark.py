"""Tests for explicit Yahoo benchmark instrument selection."""

import json

import pytest

from indexlens.prices.yahoo import YahooValidationError, chart_records
from tests.test_yahoo import END, FIXTURE, START


@pytest.mark.parametrize("symbol,instrument", [("^GSPC", "INDEX"), ("SPY", "ETF")])
def test_benchmark_instruments_require_explicit_selection(
    symbol: str, instrument: str
) -> None:
    raw = json.loads(FIXTURE.read_bytes())
    raw["chart"]["result"][0]["meta"].update(symbol=symbol, instrumentType=instrument)
    payload = json.dumps(raw).encode()
    with pytest.raises(YahooValidationError):
        chart_records(payload, symbol, START, END)
    rows, meta = chart_records(payload, symbol, START, END, instrument_type=instrument)
    assert rows and meta["instrumentType"] == instrument
