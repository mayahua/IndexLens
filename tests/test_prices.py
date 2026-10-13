"""Tests for shared price normalization, missingness, metadata, and batching."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from indexlens.prices.metadata import PriceMetadata
from indexlens.prices.normalization import batches, normalize_price_record


def bar(**changes: object) -> dict[str, object]:
    return (
        dict(
            provider_symbol="DEMO_A",
            observation_date="2026-09-21",
            open="100",
            high="105",
            low="98",
            close="103",
            volume="0",
        )
        | changes
    )


def test_provider_scalars_and_csv_strings_normalize_equally() -> None:
    csv_record = normalize_price_record(bar())
    provider = normalize_price_record(
        bar(
            observation_date=date(2026, 9, 21),
            open=100.0,
            high=105,
            low=Decimal("98.000"),
            close=103.0,
            volume=0,
        )
    )
    assert provider == csv_record
    assert provider.prices_usable and provider.volume_usable
    assert provider.issues == ("unresolved_mapping", "unverified_price_basis")


@pytest.mark.parametrize(
    "field,value,issue",
    [
        ("volume", "", "missing_volume"),
        ("volume", None, "missing_volume"),
        ("volume", "oops", "invalid_volume"),
        ("volume", "-1", "invalid_volume"),
        ("volume", "1.2", "invalid_volume"),
        ("volume", True, "invalid_volume"),
        ("open", "", "missing_open"),
        ("close", "bad", "invalid_close"),
        ("low", "0", "invalid_low"),
        ("high", "-1", "invalid_high"),
        ("close", "NaN", "nonfinite_close"),
        ("open", "Infinity", "nonfinite_open"),
        ("volume", float("nan"), "nonfinite_volume"),
        ("high", float("-inf"), "nonfinite_high"),
    ],
)
def test_invalid_and_missing_values_remain_distinct(
    field: str,
    value: object,
    issue: str,
) -> None:
    record = normalize_price_record(bar(**{field: value}))
    assert record.has_key
    assert record.values[field] is None
    assert issue in record.issues
    assert record.prices_usable == (field == "volume")
    assert record.volume_usable == (field != "volume" and issue.startswith("missing_"))


@pytest.mark.parametrize(
    "changes",
    [
        {"close": "106"},
        {"open": "97"},
        {"low": "106"},
        {"high": "97"},
    ],
)
def test_impossible_ohlc_keeps_values_but_blocks_prices(
    changes: dict[str, str],
) -> None:
    record = normalize_price_record(bar(**changes))
    assert not record.prices_usable
    assert record.has_key and not record.volume_usable
    assert "invalid_ohlc" in record.issues
    assert all(value is not None for value in record.values.values())


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_symbol": ""},
        {"provider_symbol": None},
        {"observation_date": "2026-02-30"},
        {"observation_date": "20260921"},
        {"observation_date": "2026-09-21T00:00:00Z"},
        {"observation_date": None},
        {"observation_date": datetime(2026, 9, 21, tzinfo=timezone.utc)},
    ],
)
def test_bad_keys_prevent_observations(changes: dict[str, object]) -> None:
    assert not normalize_price_record(bar(**changes)).has_key


def test_no_rounding_or_implicit_mapping_approval() -> None:
    value = "100.12345678901234567890123456789012345"
    record = normalize_price_record(
        bar(open=value, security_id="claimed", mapping_reference="unreviewed")
    )
    assert Decimal(record.values["open"] or "0") == Decimal(value)
    assert "unresolved_mapping" in record.issues
    assert normalize_price_record(bar(volume="0")).values["volume"] == "0"
    assert normalize_price_record(bar(open="100.00")).values == (
        normalize_price_record(bar(open=100)).values
    )


def test_batch_boundaries_and_empty_input() -> None:
    assert list(batches([], 2)) == []
    assert list(batches(iter(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    with pytest.raises(ValueError):
        list(batches([], 0))


@pytest.mark.parametrize(
    "changes",
    [
        {"data_kind": "mixed"},
        {"provider": " "},
        {"price_basis": ""},
        {"retrieved_at": "2026-10-12T12:00:00"},
        {"adjustment_settings": None},
        {"source_reference": ""},
        {"source_notes": ""},
    ],
)
def test_metadata_requires_explicit_provenance(changes: dict[str, Any]) -> None:
    raw = dict(
        dataset_id="synthetic-v1",
        data_kind="synthetic",
        provider="fixture",
        price_basis="unreviewed",
        retrieved_at="2026-10-12T12:00:00Z",
        source_reference="fixture-v1",
        source_notes="Invented; review 2026-10-12.",
        adjustment_settings={},
    )
    with pytest.raises(ValueError):
        PriceMetadata.from_dict(raw | changes)
