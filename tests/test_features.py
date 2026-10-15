"""Tests for hand-calculated features, complete windows, chronology and ties."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from indexlens.features.core import Observation, calculate, momentum_order

START = date(2026, 1, 1)


def observations() -> list[Observation]:
    # 19 zero returns then 10%; sample variance = 0.0005, volume mean = 10.5.
    return [
        Observation(
            START + timedelta(days=i), Decimal(100 if i < 20 else 110), Decimal(i)
        )
        for i in range(21)
    ]


def test_hand_calculated_formulas_and_chronology() -> None:
    rows = observations()
    result = calculate(list(reversed(rows)), rows[-1].day)
    assert result.values["return_20"] == Decimal("0.1")
    assert float(result.values["volatility_20"] or 0) == pytest.approx(0.0005**0.5)
    assert result.values["average_volume_20"] == Decimal("10.5")
    assert result.unavailable == {}
    assert len(result.windows["return_20"]) == 21
    assert len(result.windows["average_volume_20"]) == 20
    # Daily return is simple, not log, and volatility is unannualized.


@pytest.mark.parametrize("length", [0, 1, 19, 20, 21])
def test_warm_up_boundaries(length: int) -> None:
    rows = observations()[:length]
    result = calculate(rows, rows[-1].day if rows else START)
    assert (result.values["return_20"] is not None) == (length >= 21)
    assert (result.values["volatility_20"] is not None) == (length >= 21)
    assert (result.values["average_volume_20"] is not None) == (length >= 20)


@pytest.mark.parametrize(
    ("change", "prices", "volume"),
    [
        ({"volume": None, "volume_usable": False}, True, False),
        ({"volume": Decimal(0)}, True, True),
        ({"volume": Decimal("1.5")}, True, False),
        ({"close": None, "close_usable": False}, False, True),
        ({"close": Decimal("NaN")}, False, True),
        ({"close": Decimal(0)}, False, True),
        ({"close_usable": False, "volume_usable": False}, False, False),
        ({"blockers": ("conflicting_observation",)}, False, False),
        ({"blockers": ("outside_mapping_dates",)}, False, False),
        ({"blockers": ("unresolved_basis",)}, False, False),
    ],
)
def test_bad_interior_row_stays_in_window(
    change: dict[str, object], prices: bool, volume: bool
) -> None:
    rows = observations()
    rows.insert(0, replace(rows[0], day=START - timedelta(days=1)))
    rows[10] = replace(rows[10], **change)  # type: ignore[arg-type]
    result = calculate(rows, rows[-1].day)
    assert (result.values["return_20"] is not None) == prices
    assert (result.values["volatility_20"] is not None) == prices
    assert (result.values["average_volume_20"] is not None) == volume
    assert result.windows["return_20"][0] == START.isoformat()


def test_first_price_date_is_not_in_volume_window() -> None:
    rows = observations()
    rows[0] = replace(rows[0], blockers=("outside_mapping_dates",))
    result = calculate(rows, rows[-1].day)
    assert result.values["return_20"] is None
    assert result.values["average_volume_20"] == Decimal("10.5")


def test_observed_dates_future_cutoff_and_no_stale_reference() -> None:
    rows = observations()
    # An absent calendar day is not an observed row and is not manufactured.
    rows = [replace(row, day=row.day + timedelta(days=i)) for i, row in enumerate(rows)]
    reference = rows[-1].day
    future = replace(rows[-1], day=reference + timedelta(days=2), close=Decimal(999))
    assert calculate(rows + [future], reference).values["return_20"] == Decimal("0.1")
    assert all(
        v is None
        for v in calculate(rows, reference + timedelta(days=1)).values.values()
    )
    with pytest.raises(ValueError, match="Duplicate"):
        calculate(rows + [rows[0]], reference)


def test_identity_ties_and_unavailable_entries() -> None:
    rows = observations()
    valid = calculate(rows, rows[-1].day)
    empty = calculate([], rows[-1].day)
    results = {
        ("B", "0001"): valid,
        ("A", "0002"): valid,
        ("A", "0001"): valid,
        ("C", "0003"): empty,
    }
    assert momentum_order(results) == [("A", "0001"), ("A", "0002"), ("B", "0001")]
    assert len(results) == 4


def test_review_rejects_malformed_or_ambiguous_declarations() -> None:
    from indexlens.features.review import Review

    with pytest.raises(ValueError):
        Review.from_dict({"review_version": "unknown"})
