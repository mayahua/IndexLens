"""Versioned complete-window features on chronological observed sessions."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, DecimalException, localcontext
from typing import Any

FEATURE_VERSION = "observed-20-v2"
FEATURES = ("return_20", "volatility_20", "average_volume_20")


@dataclass(frozen=True)
class Observation:
    day: date
    close: Decimal | None
    volume: Decimal | None
    close_usable: bool = True
    volume_usable: bool = True
    blockers: tuple[str, ...] = ()
    provenance: tuple[dict[str, Any], ...] = ()


@dataclass
class FeatureResult:
    values: dict[str, Decimal | None] = field(
        default_factory=lambda: dict.fromkeys(FEATURES)
    )
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    windows: dict[str, list[str]] = field(default_factory=dict)


def calculate(observations: list[Observation], day: date) -> FeatureResult:
    """Select windows before checking validity; never backfill or skip bad rows.

    Callers provide one dataset/provider/security/basis group with dated approval
    blockers. Duplicate dates are rejected; future observations are ignored.
    """
    rows = sorted((row for row in observations if row.day <= day), key=lambda r: r.day)
    if len({row.day for row in rows}) != len(rows):
        raise ValueError("Duplicate observation dates must be resolved by ingestion.")
    rows = rows[-21:]
    result = FeatureResult()
    result.inputs = [
        dict(
            observation_date=row.day.isoformat(),
            close=str(row.close) if row.close is not None else None,
            volume=str(row.volume) if row.volume is not None else None,
            close_usable=row.close_usable,
            volume_usable=row.volume_usable,
            blockers=list(row.blockers),
            provenance=list(row.provenance),
        )
        for row in rows
    ]
    for feature in FEATURES:
        volume = feature == "average_volume_20"
        size = 20 if volume else 21
        window = rows[-size:]
        result.windows[feature] = [row.day.isoformat() for row in window]
        reasons: set[str] = set()
        if not rows or rows[-1].day != day:
            reasons.add("missing_reference_observation")
        if len(window) < size:
            reasons.add("warm_up")
        values: list[Decimal] = []
        for row in window:
            reasons.update(row.blockers)
            value = row.volume if volume else row.close
            usable = row.volume_usable if volume else row.close_usable
            if value is None:
                reasons.add("missing_volume" if volume else "missing_close")
            elif not value.is_finite() or value < 0 or (not volume and value == 0):
                reasons.add("invalid_volume" if volume else "invalid_close")
            elif volume and value != value.to_integral_value():
                reasons.add("invalid_volume")
            else:
                values.append(value)
            if not usable:
                reasons.add("unusable_volume" if volume else "unusable_close")
        if reasons:
            result.unavailable[feature] = sorted(reasons)
            continue
        try:
            with localcontext() as context:
                context.prec = 34
                if volume:
                    value = sum(values, Decimal(0)) / 20
                elif feature == "return_20":
                    value = values[-1] / values[0] - 1
                else:
                    returns = [
                        b / a - 1 for a, b in zip(values[:-1], values[1:], strict=True)
                    ]
                    mean = sum(returns, Decimal(0)) / 20
                    value = (
                        sum(((r - mean) * (r - mean) for r in returns), Decimal(0)) / 19
                    ).sqrt()
                if not value.is_finite():
                    raise ValueError("Nonfinite feature.")
                result.values[feature] = value
        except (DecimalException, ValueError):
            result.unavailable[feature] = ["numeric_range"]
    return result


def momentum_order(
    results: dict[tuple[str, str], FeatureResult],
) -> list[tuple[str, str]]:
    """Descending return, then exact source symbol and CIK; unavailable excluded.

    Callers must retain unavailable identities in their reported universe.
    """
    eligible = [
        (identity, result.values["return_20"])
        for identity, result in results.items()
        if result.values["return_20"] is not None
    ]
    return [
        identity
        for identity, _ in sorted(
            eligible, key=lambda item: (item[1].copy_negate(), item[0])
        )
    ]
