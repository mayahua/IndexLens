"""Tests for financial formulas, complete windows, alignment and historical bounds."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest

from indexlens.features.context import calculate_context
from indexlens.features.core import Observation, calculate

DAY = date(2026, 7, 31)


def history() -> list[Observation]:
    return [
        Observation(DAY - timedelta(days=120 - i), Decimal(100), Decimal(10))
        for i in range(121)
    ]


def context(
    stock: list[Observation], benchmark: list[Observation] | None = None
) -> dict:  # type: ignore[type-arg]
    return calculate_context(
        stock,
        history() if benchmark is None else benchmark,
        DAY,
        dollar_volume_approved=True,
        comparison_approved=True,
    )


def test_hand_calculated_indicators_and_legacy_parity() -> None:
    stock, market = history(), history()
    stock[-1] = replace(stock[-1], close=Decimal(90), volume=Decimal(20))
    market[-1] = replace(market[-1], close=Decimal(110))
    result = context(stock, market)["indicators"]
    for n in (20, 60, 120):
        assert Decimal(result[f"return_{n}"]["value"]) == Decimal("-0.1")
        assert Decimal(result[f"benchmark_return_{n}"]["value"]) == Decimal("0.1")
        assert Decimal(result[f"excess_return_{n}"]["value"]) == Decimal("-0.2")
    assert Decimal(result["average_dollar_volume_20"]["value"]) == 1040
    with localcontext() as ctx:
        ctx.prec = 34
        assert Decimal(result["average_dollar_volume_60"]["value"]) == (
            Decimal(59000 + 1800) / 60
        )
        assert (
            Decimal(result["volatility_20"]["value"]) == (Decimal("0.01") / 20).sqrt()
        )
    assert Decimal(result["relative_volatility_20"]["value"]) == 1
    assert Decimal(result["drawdown_120"]["value"]) == Decimal("-0.1")
    assert float(result["dollar_volume_20_to_60_ratio"]["value"]) == pytest.approx(
        1040 / (60800 / 60)
    )
    legacy = calculate(stock, DAY)
    assert result["return_20"]["value"] == str(legacy.values["return_20"])
    assert result["volatility_20"]["value"] == str(legacy.values["volatility_20"])


def test_exact_boundaries_and_future_rows() -> None:
    stock = history()
    stock[0] = replace(stock[0], close=Decimal(200))
    result = context(stock)
    indicators = result["indicators"]
    assert indicators["drawdown_120"]["value"] == "0"
    assert indicators["return_120"]["value"] == "-0.5"
    assert len(indicators["return_60"]["observed_dates"]) == 61
    assert len(indicators["average_dollar_volume_60"]["observed_dates"]) == 60
    assert (
        context(
            stock
            + [replace(stock[-1], day=DAY + timedelta(days=1), close=Decimal(999))]
        )
        == result
    )
    assert "warm_up" in context(stock[1:])["indicators"]["return_120"]["unavailable"]
    assert context(stock[1:])["indicators"]["drawdown_120"]["value"] == "0"
    assert (
        "missing_reference_observation"
        in context(stock[:-1])["indicators"]["return_20"]["unavailable"]
    )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("close", None, "missing_close"),
        ("close", Decimal(0), "invalid_close"),
        ("close", Decimal("NaN"), "invalid_close"),
        ("volume", None, "missing_volume"),
        ("volume", Decimal(-1), "invalid_volume"),
        ("volume", Decimal("Infinity"), "invalid_volume"),
        ("volume", Decimal("1.5"), "invalid_volume"),
    ],
)
def test_bad_rows_keep_their_positions(
    field: str, value: Decimal | None, reason: str
) -> None:
    stock = history()
    stock[-10] = (
        replace(stock[-10], close=value)
        if field == "close"
        else replace(stock[-10], volume=value)
    )
    result = context(stock)["indicators"]
    assert reason in result["average_dollar_volume_20"]["unavailable"]
    assert len(result["average_dollar_volume_20"]["observed_dates"]) == 20
    assert (result["return_20"]["value"] is None) == (field == "close")


def test_missing_benchmark_dates_do_not_create_an_intersection_window() -> None:
    stock, market = history(), history()
    del market[-10]
    result = context(stock, market)["indicators"]
    assert result["return_20"]["value"] == "0"
    assert "misaligned_benchmark_dates" in result["excess_return_20"]["unavailable"]
    assert result["relative_volatility_20"]["value"] is None
    # A missing stock date is also a mismatch, even with matching endpoints.
    stock = history()
    del stock[-10]
    assert context(stock)["indicators"]["excess_return_20"]["value"] is None
    missing = context(history(), [])["indicators"]
    assert "benchmark_warm_up" in missing["excess_return_60"]["unavailable"]


def test_review_gates_zero_denominators_and_duplicate_dates() -> None:
    stock = history()
    result = calculate_context(stock, history(), DAY)["indicators"]
    assert result["return_120"]["value"] == "0"
    assert result["average_dollar_volume_20"]["value"] is None
    assert result["excess_return_20"]["value"] is None
    result = context([replace(r, volume=Decimal(0)) for r in stock])["indicators"]
    assert result["average_dollar_volume_20"]["value"] == "0"
    assert result["dollar_volume_20_to_60_ratio"]["unavailable"] == ["zero_denominator"]
    assert result["relative_volatility_20"]["unavailable"] == ["zero_denominator"]
    stock[-1] = replace(stock[-1], blockers=("outside_mapping_dates",))
    assert context(stock)["indicators"]["return_60"]["value"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        context(history() + [history()[-1]])


def test_split_consistent_price_volume_product() -> None:
    original = history()
    adjusted = [
        replace(r, close=r.close / 2, volume=r.volume * 2)
        for r in original
        if r.close is not None and r.volume is not None
    ]
    a, b = context(original)["indicators"], context(adjusted)["indicators"]
    for name in ("return_120", "drawdown_120", "average_dollar_volume_60"):
        assert a[name]["value"] == b[name]["value"]
    # Dividend-adjusted Close must not be substituted for the reviewed close.
    assert (
        context([replace(r, close=Decimal(95)) for r in original])["indicators"][
            "average_dollar_volume_20"
        ]["value"]
        != a["average_dollar_volume_20"]["value"]
    )
