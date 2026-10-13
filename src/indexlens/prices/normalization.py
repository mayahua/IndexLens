"""Normalize adapter fields without filling gaps or approving security mappings."""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import islice
from typing import TypeVar

VALUE_FIELDS = ("open", "high", "low", "close", "volume")
T = TypeVar("T")


@dataclass(frozen=True)
class PriceRecord:
    provider_symbol: str | None
    observation_date: date | None
    values: dict[str, str | None]
    issues: tuple[str, ...]
    prices_usable: bool
    volume_usable: bool

    @property
    def has_key(self) -> bool:
        return self.provider_symbol is not None and self.observation_date is not None


def batches(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Yield nonempty batches, including the final partial batch."""
    if size < 1:
        raise ValueError("Batch size must be positive.")
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def _number(value: object, field: str) -> tuple[str | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"missing_{field}"
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None, f"invalid_{field}"
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None, f"invalid_{field}"
    if not number.is_finite():
        return None, f"nonfinite_{field}"
    if field == "volume":
        if number < 0 or number != number.to_integral_value():
            return None, "invalid_volume"
    elif number <= 0:
        return None, f"invalid_{field}"
    # Avoid rounding through binary floats or the Decimal arithmetic context.
    # Scientific notation also avoids expanding very large provider exponents.
    sign, digits, exponent = number.as_tuple()
    assert isinstance(exponent, int)
    coefficient = "".join(str(digit) for digit in digits).rstrip("0")
    if not coefficient:
        return "0", None
    exponent += len(digits) - len(coefficient)
    canonical = ("-" if sign else "") + coefficient + f"e{exponent}"
    return canonical, None


def normalize_price_record(fields: Mapping[str, object]) -> PriceRecord:
    """Accept logical provider/CSV fields; preserve raw evidence in the adapter.

    Invalid keys reject canonical observation creation. Invalid or missing values
    retain the date and issue information. Mapping claims never approve a join.
    """
    issues: list[str] = []
    symbol_value = fields.get("provider_symbol")
    symbol = symbol_value.strip() if isinstance(symbol_value, str) else None
    if not symbol:
        symbol = None
        issues.append("missing_provider_symbol")
    day = None
    day_value = fields.get("observation_date")
    if type(day_value) is date:
        day = day_value
    elif isinstance(day_value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_value):
        try:
            day = date.fromisoformat(day_value)
        except ValueError:
            pass
    if day is None:
        issues.append("invalid_observation_date")
    values: dict[str, str | None] = {}
    for field in VALUE_FIELDS:
        values[field], issue = _number(fields.get(field), field)
        if issue:
            issues.append(issue)
    prices = [
        Decimal(value) for f in VALUE_FIELDS[:4] if (value := values[f]) is not None
    ]
    prices_usable = len(prices) == 4
    if prices_usable:
        opening, high, low, close = prices
        if high < max(opening, low, close) or low > min(opening, high, close):
            issues.append("invalid_ohlc")
            prices_usable = False
    invalid_prices = any(
        issue == "invalid_ohlc"
        or any(
            issue == f"{prefix}_{field}"
            for prefix in ("invalid", "nonfinite")
            for field in VALUE_FIELDS[:4]
        )
        for issue in issues
    )
    issues.extend(("unresolved_mapping", "unverified_price_basis"))
    return PriceRecord(
        symbol,
        day,
        values,
        tuple(issues),
        prices_usable,
        values["volume"] is not None and not invalid_prices,
    )
