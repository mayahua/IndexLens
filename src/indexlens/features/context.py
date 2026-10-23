"""Descriptive financial indicators from reviewed, dated price observations."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, DecimalException, localcontext
from functools import partial
from typing import Any

from indexlens.prices.metadata import fingerprint

from .core import Observation, calculate

CONTEXT_VERSION = "financial-context-v1"
LIMITATIONS = [
    "Descriptive indicators are separate from the saved model's fitted predictors.",
    "Close-based price returns exclude dividend reinvestment. Provider revisions "
    "and corporate-action adjustments can affect historical values; retrieval "
    "dates do not certify point-in-time availability.",
    "Windows count observed dates. Entirely absent sessions are not filled; "
    "volatility can include returns across gaps and is unannualized.",
    "Closing price times volume is an approximate trading-activity measure, "
    "not transaction turnover or the official S&P liquidity eligibility test.",
    "The short dollar-volume mean overlaps the longer mean; their ratio compares "
    "recent activity with a broader baseline, not consecutive periods.",
    "Trailing drawdown is distance below a trailing closing high, not maximum "
    "peak-to-trough loss or a forecast. Volatility measures both gains and losses.",
    "Price indicators do not establish fundamentals, index eligibility, committee "
    "intentions or future performance. Addition criteria and existing-member "
    "removal decisions differ; committee discretion remains relevant.",
]


def selected(rows: list[Observation], day: date) -> list[Observation]:
    result = sorted((r for r in rows if r.day <= day), key=lambda r: r.day)
    if len({r.day for r in result}) != len(result):
        raise ValueError("Duplicate observation dates must be resolved by ingestion.")
    return result[-121:]


def values(
    rows: list[Observation], size: int, day: date, *, volume: bool = False
) -> tuple[list[Decimal], list[str]]:
    reasons = set()
    if len(rows) < size:
        reasons.add("warm_up")
    if not rows or rows[-1].day != day:
        reasons.add("missing_reference_observation")
    result = []
    for row in rows:
        reasons.update(row.blockers)
        value = row.volume if volume else row.close
        name = "volume" if volume else "close"
        if value is None:
            reasons.add("missing_" + name)
        elif (
            not value.is_finite()
            or value < 0
            or (not volume and value == 0)
            or (volume and value != value.to_integral_value())
        ):
            reasons.add("invalid_" + name)
        else:
            result.append(value)
        if not (row.volume_usable if volume else row.close_usable):
            reasons.add("unusable_" + name)
    return result, sorted(reasons)


def input_records(rows: list[Observation]) -> list[dict[str, Any]]:
    return [
        dict(
            observation_date=str(r.day),
            close=str(r.close) if r.close is not None else None,
            volume=str(r.volume) if r.volume is not None else None,
            close_usable=r.close_usable,
            volume_usable=r.volume_usable,
            blockers=list(r.blockers),
            provenance=list(r.provenance),
        )
        for r in rows
    ]


def price_return(closes: list[Decimal]) -> Decimal:
    return closes[-1] / closes[0] - 1


def dollar_mean(closes: list[Decimal], volumes: list[Decimal]) -> Decimal:
    return sum((p * v for p, v in zip(closes, volumes, strict=True)), Decimal(0)) / len(
        closes
    )


def supplied(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("NaN")


def calculate_context(
    observations: list[Observation],
    benchmark: list[Observation],
    day: date,
    *,
    dollar_volume_approved: bool = False,
    comparison_approved: bool = False,
    dollar_volume_dates: set[date] | None = None,
) -> dict[str, Any]:
    """Use complete stock windows and identical benchmark date sequences.

    Review policies approve USD Close/volume compatibility and price-return
    comparison separately. All rows retain their places, including invalid rows.
    """
    rows, market = selected(observations, day), selected(benchmark, day)
    indicators: dict[str, Any] = {}

    def add(
        name: str,
        window: list[Observation],
        size: int,
        unit: str,
        reasons: list[str],
        compute: Callable[[], Decimal],
    ) -> None:
        value = None
        if not reasons:
            try:
                with localcontext() as ctx:
                    ctx.prec = 34
                    number = compute()
                    if not number.is_finite():
                        raise ValueError("Nonfinite indicator.")
                    value = str(number)
            except (DecimalException, ValueError):
                reasons = ["numeric_range"]
        indicators[name] = dict(
            value=value,
            unit=unit,
            required_observations=size,
            observed_dates=[str(r.day) for r in window],
            unavailable=sorted(set(reasons)),
        )

    legacy = calculate(rows, day)
    for name in ("return_20", "volatility_20"):
        value = legacy.values[name]
        indicators[name] = dict(
            value=str(value) if value is not None else None,
            unit="fraction" if name == "return_20" else "daily_sample_std",
            required_observations=21,
            observed_dates=legacy.windows[name],
            unavailable=legacy.unavailable.get(name, []),
        )
    for n in (60, 120):
        window = rows[-(n + 1) :]
        closes, reasons = values(window, n + 1, day)
        add(
            f"return_{n}",
            window,
            n + 1,
            "fraction",
            reasons,
            partial(price_return, closes),
        )
    for n in (20, 60):
        window = rows[-n:]
        closes, reasons = values(window, n, day)
        volumes, volume_reasons = values(window, n, day, volume=True)
        reasons += volume_reasons
        if dollar_volume_dates is not None and any(
            r.day not in dollar_volume_dates for r in window
        ):
            reasons.append("outside_dollar_volume_review")
        if not dollar_volume_approved:
            reasons.append("unreviewed_dollar_volume_basis")
        add(
            f"average_dollar_volume_{n}",
            window,
            n,
            "USD_per_session",
            reasons,
            partial(dollar_mean, closes, volumes),
        )
    window = rows[-120:]
    closes, reasons = values(window, 120, day)
    add(
        "drawdown_120",
        window,
        120,
        "fraction",
        reasons,
        lambda: closes[-1] / max(closes) - 1,
    )

    for n in (20, 60, 120):
        window = rows[-(n + 1) :]
        aligned = [r for r in market if window and r.day >= window[0].day]
        _, stock_reasons = values(window, n + 1, day)
        closes, reasons = values(aligned, n + 1, day)
        reasons = ["benchmark_" + r for r in reasons]
        if [r.day for r in window] != [r.day for r in aligned]:
            reasons.append("misaligned_benchmark_dates")
        if not comparison_approved:
            reasons.append("unreviewed_benchmark_comparison")
        add(
            f"benchmark_return_{n}",
            window,
            n + 1,
            "fraction",
            reasons + stock_reasons,
            partial(price_return, closes),
        )
        if n == 20:
            volatility = calculate(aligned, day)
            vol = volatility.values["volatility_20"]
            add(
                "benchmark_volatility_20",
                window,
                21,
                "daily_sample_std",
                reasons
                + stock_reasons
                + volatility.unavailable.get("volatility_20", []),
                partial(supplied, vol),
            )

    def derived(name: str, left: str, right: str, *, ratio: bool) -> None:
        a, b = indicators[left], indicators[right]
        reasons = a["unavailable"] + b["unavailable"]
        if b["value"] is not None and ratio and Decimal(b["value"]) == 0:
            reasons.append("zero_denominator")
        add(
            name,
            [],
            0,
            "ratio" if ratio else "fraction_difference",
            reasons,
            lambda: (
                Decimal(a["value"]) / Decimal(b["value"])
                if ratio
                else Decimal(a["value"]) - Decimal(b["value"])
            ),
        )
        indicators[name]["components"] = [left, right]
        indicators[name]["observed_dates"] = b["observed_dates"]
        indicators[name]["required_observations"] = b["required_observations"]

    for n in (20, 60, 120):
        derived(
            f"excess_return_{n}", f"return_{n}", f"benchmark_return_{n}", ratio=False
        )
    derived(
        "relative_volatility_20", "volatility_20", "benchmark_volatility_20", ratio=True
    )
    derived(
        "dollar_volume_20_to_60_ratio",
        "average_dollar_volume_20",
        "average_dollar_volume_60",
        ratio=True,
    )
    inputs = dict(stock=input_records(rows), benchmark=input_records(market))
    return dict(
        context_version=CONTEXT_VERSION,
        observation_date=str(day),
        indicators=indicators,
        inputs=inputs,
        input_sha256=fingerprint(inputs),
        coverage={
            key: dict(
                observations=len(items),
                first_date=items[0]["observation_date"] if items else None,
                last_date=items[-1]["observation_date"] if items else None,
                reference_observed=bool(items)
                and items[-1]["observation_date"] == str(day),
            )
            for key, items in inputs.items()
        },
        limitations=LIMITATIONS,
    )
