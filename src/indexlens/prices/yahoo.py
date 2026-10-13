"""Translate saved Yahoo chart evidence without yfinance's missing-value filling."""

import csv
import importlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

from .normalization import normalize_price_record

SETTINGS = dict(
    auto_adjust=False,
    back_adjust=False,
    repair=False,
    actions=True,
    keepna=True,
    rounding=False,
    interval="1d",
)
BASIS = "yahoo_returned_ohlc_unadjusted_library_raw_unverified"
YFINANCE_VERSION = "1.7.0"
COLUMNS = (
    "provider_symbol",
    "observation_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "Adj Close",
    "raw_timestamp",
    "corporate_actions",
)


class YahooValidationError(ValueError):
    """A safe machine-readable reason why chart evidence cannot be loaded."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def chart_records(
    payload: bytes,
    symbol: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use exchange-local daily dates, retaining nulls and raw action evidence."""
    try:
        chart = json.loads(payload, parse_float=Decimal)["chart"]
        if chart.get("error") or len(chart["result"]) != 1:
            raise YahooValidationError("provider_chart_error")
        result = chart["result"][0]
        metadata = result["meta"]
        if metadata["symbol"].upper() != symbol.upper():
            raise YahooValidationError("provider_symbol_mismatch")
        if metadata.get("instrumentType") != "EQUITY":
            raise YahooValidationError("unexpected_instrument_type")
        tz = ZoneInfo(metadata["exchangeTimezoneName"])
        timestamps = result.get("timestamp") or []
        if not timestamps:
            raise YahooValidationError("empty_chart")
        quote = result["indicators"]["quote"][0]
        arrays = {
            field: quote.get(field, [None] * len(timestamps))
            for field in ("open", "high", "low", "close", "volume")
        }
        adjusted = result["indicators"].get("adjclose", [{}])[0]
        arrays["Adj Close"] = adjusted.get("adjclose", [None] * len(timestamps))
        if any(
            not isinstance(v, list) or len(v) != len(timestamps)
            for v in arrays.values()
        ):
            raise YahooValidationError("chart_shape_invalid")
        actions: dict[str, list[dict[str, Any]]] = {}
        for kind, events in result.get("events", {}).items():
            for event in events.values():
                action_day = (
                    datetime.fromtimestamp(int(event["date"]), tz).date().isoformat()
                )
                actions.setdefault(action_day, []).append({"kind": kind, **event})
        rows = []
        for position, stamp in enumerate(timestamps):
            day = datetime.fromtimestamp(int(stamp), tz).date()
            if not start <= day < end:
                raise YahooValidationError("out_of_range_dates")
            rows.append(
                dict(
                    provider_symbol=symbol,
                    observation_date=day.isoformat(),
                    raw_timestamp=str(stamp),
                    corporate_actions=json.dumps(
                        actions.get(day.isoformat(), []), sort_keys=True, default=str
                    ),
                    **{field: values[position] for field, values in arrays.items()},
                )
            )
        safe_metadata = {
            key: metadata.get(key)
            for key in (
                "symbol",
                "shortName",
                "longName",
                "currency",
                "exchangeName",
                "instrumentType",
                "exchangeTimezoneName",
                "firstTradeDate",
            )
        }
        return rows, safe_metadata
    except (AttributeError, KeyError, IndexError, TypeError, OverflowError) as exc:
        raise YahooValidationError("chart_shape_invalid") from exc


def logical_csv(rows: list[dict[str, Any]]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def session_coverage(
    rows: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, Any]:
    """Report US equity session gaps without manufacturing any observations."""
    calendars = importlib.import_module("exchange_calendars")
    calendar = calendars.get_calendar("XNYS", start=start, end=end)
    expected = [
        d.date().isoformat() for d in calendar.sessions if start <= d.date() < end
    ]
    records = [normalize_price_record(row) for row in rows]
    observed = {
        r.observation_date.isoformat()
        for r in records
        if r.observation_date is not None
    }
    missing = [day for day in expected if day not in observed]
    ranges: list[dict[str, Any]] = []
    previous_missing_position = -2
    for position, day in enumerate(expected):
        if day in observed:
            continue
        if ranges and position == previous_missing_position + 1:
            ranges[-1]["end"] = day
            ranges[-1]["sessions"] += 1
        else:
            ranges.append(dict(start=day, end=day, sessions=1))
        previous_missing_position = position
    return dict(
        calendar="XNYS (US equity sessions; not a listing/identity approval)",
        requested_start=start.isoformat(),
        requested_end_exclusive=end.isoformat(),
        expected_sessions=len(expected),
        observed_dates=len(observed),
        first_date=min(observed) if observed else None,
        last_date=max(observed) if observed else None,
        missing_sessions=len(missing),
        missing_session_ranges=ranges,
        unexpected_dates=sorted(observed - set(expected)),
        duplicate_dates=len(records) - len(observed),
        rows=len(records),
        usable_price_rows=sum(r.prices_usable for r in records),
        usable_volume_rows=sum(r.volume_usable for r in records),
        rows_with_value_issues=sum(
            not r.prices_usable or not r.volume_usable for r in records
        ),
        mapping_status="pending_historical_review",
        feature_join_approved=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
