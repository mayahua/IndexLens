"""Tests for Yahoo response normalization, evidence, mapping checks and coverage."""

import csv
import json
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from indexlens.prices.ingestion import Options, validate_manifest
from indexlens.prices.normalization import normalize_price_record
from indexlens.prices.yahoo import (
    YahooValidationError,
    chart_records,
    logical_csv,
    session_coverage,
)

FIXTURE = Path(__file__).parent / "fixtures/yahoo_synthetic.json"
START, END = date(2026, 9, 21), date(2026, 9, 25)


def test_raw_response_and_csv_normalize_equally_without_filling() -> None:
    rows, metadata = chart_records(FIXTURE.read_bytes(), "DEMO_A", START, END)
    expected = list(csv.DictReader(StringIO(FIXTURE.with_suffix(".csv").read_text())))
    assert [normalize_price_record(r) for r in rows] == [
        normalize_price_record(r) for r in expected
    ]
    from_csv = list(csv.DictReader(StringIO(logical_csv(rows).decode())))
    assert [normalize_price_record(r) for r in rows] == [
        normalize_price_record(r) for r in from_csv
    ]
    assert normalize_price_record(rows[0]).values["volume"] == "0"
    assert normalize_price_record(rows[1]).values["volume"] is None
    assert from_csv[1]["volume"] == ""
    assert from_csv[0]["Adj Close"] == "102.5"
    assert "dividends" in from_csv[1]["corporate_actions"]
    assert "splits" in from_csv[1]["corporate_actions"]
    assert metadata["symbol"] == "DEMO_A"
    assert "unresolved_mapping" in normalize_price_record(rows[0]).issues


def test_calendar_coverage_counts_sessions_without_filling() -> None:
    rows, _ = chart_records(FIXTURE.read_bytes(), "DEMO_A", START, END)
    coverage = session_coverage(rows, START, END)
    assert coverage["expected_sessions"] == 4
    assert coverage["observed_dates"] == 3
    assert coverage["missing_session_ranges"] == [
        dict(start="2026-09-24", end="2026-09-24", sessions=1)
    ]
    assert coverage["usable_price_rows"] == 2
    assert coverage["usable_volume_rows"] == 1
    assert not coverage["feature_join_approved"]


@pytest.mark.parametrize("problem", ["symbol", "empty", "length", "date", "instrument"])
def test_unusable_chart_is_explicitly_rejected(problem: str) -> None:
    payload = json.loads(FIXTURE.read_bytes())
    result = payload["chart"]["result"][0]
    if problem == "symbol":
        result["meta"]["symbol"] = "OTHER"
    elif problem == "empty":
        result["timestamp"] = []
    elif problem == "length":
        result["indicators"]["quote"][0]["volume"] = [1]
    elif problem == "instrument":
        result["meta"]["instrumentType"] = "MUTUALFUND"
    else:
        result["timestamp"][0] = int(
            datetime(2025, 1, 1, tzinfo=ZoneInfo("America/New_York")).timestamp()
        )
    with pytest.raises(YahooValidationError) as caught:
        chart_records(json.dumps(payload).encode(), "DEMO_A", START, END)
    assert (
        caught.value.code
        == {
            "symbol": "provider_symbol_mismatch",
            "empty": "empty_chart",
            "length": "chart_shape_invalid",
            "date": "out_of_range_dates",
            "instrument": "unexpected_instrument_type",
        }[problem]
    )


def manifest(count: int = 1) -> dict[str, object]:
    return dict(
        data_kind="real",
        source_sha256="a" * 64,
        eligible_source_identity_count=count,
        identities=[
            dict(symbol="DEMO_" + str(i), cik=str(i).zfill(10)) for i in range(count)
        ],
        price_request_envelope=dict(
            start_inclusive=START.isoformat(), end_exclusive=END.isoformat()
        ),
    )


def test_full_manifest_has_no_cap_and_rejects_wrong_population() -> None:
    raw = manifest(580)
    assert validate_manifest(raw)["eligible_source_identity_count"] == 580
    raw["eligible_source_identity_count"] = 579
    with pytest.raises(ValueError):
        validate_manifest(raw)


@pytest.mark.parametrize(
    "options",
    [
        Options(concurrency=0),
        Options(retries=6),
        Options(request_interval=0),
        Options(request_timeout=float("nan")),
        Options(batch_size=0),
    ],
)
def test_invalid_request_controls_fail_before_fetch(options: Options) -> None:
    with pytest.raises(ValueError):
        options.validate()


def test_daily_date_uses_exchange_timezone() -> None:
    payload = json.loads(FIXTURE.read_bytes())
    payload["chart"]["result"][0]["timestamp"][0] = int(
        datetime(2026, 9, 22, 0, 30, tzinfo=ZoneInfo("UTC")).timestamp()
    )
    rows, _ = chart_records(json.dumps(payload).encode(), "DEMO_A", START, END)
    assert rows[0]["observation_date"] == "2026-09-21"


@pytest.mark.parametrize("section", ["chart", "symbol", "quote", "adjclose", "events"])
def test_wrong_chart_container_types_are_validation_failures(section: str) -> None:
    payload = json.loads(FIXTURE.read_bytes())
    result = payload["chart"]["result"][0]
    if section == "chart":
        payload["chart"] = []
    elif section == "symbol":
        result["meta"]["symbol"] = None
    elif section in {"quote", "adjclose"}:
        result["indicators"][section] = [None]
    else:
        result["events"] = []
    with pytest.raises(YahooValidationError) as caught:
        chart_records(json.dumps(payload).encode(), "DEMO_A", START, END)
    assert caught.value.code == "chart_shape_invalid"
