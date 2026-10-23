"""Review policies and stored price evidence for financial context."""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from indexlens.db.models import PriceDataset
from indexlens.prices.metadata import canonical_json, fingerprint
from indexlens.prices.yahoo import BASIS, BENCHMARKS, SETTINGS

from .context import calculate_context
from .core import Observation
from .query import EvidenceUnavailableError, _observations
from .review import Review, _hash, _text, decision_blockers


def context_policy(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate optional descriptive-use declarations on a stock review."""
    policy = raw.get("financial_context", {})
    if not isinstance(policy, dict):
        raise ValueError("Financial context policy must be an object.")
    if policy:
        if policy.get("currency") != "USD" or policy.get("return_convention") != (
            "price_return"
        ):
            raise ValueError("Financial context requires USD price-return evidence.")
        if raw["provider"] == "yahoo" and (
            raw["price_basis"] != BASIS or raw["adjustment_settings"] != SETTINGS
        ):
            raise ValueError("Yahoo context requires the reviewed quote Close basis.")
        liquidity = policy.get("dollar_volume_review")
        if liquidity is not None:
            if not isinstance(liquidity, dict):
                raise ValueError("Dollar volume requires a basis decision.")
            Review._decision(liquidity)
            _text(liquidity, "description")
    return policy


@dataclass(frozen=True)
class BenchmarkReview:
    declarations: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BenchmarkReview":
        if raw.get("review_version") != "benchmark-review-v1":
            raise ValueError("Unsupported benchmark review version.")
        for field in (
            "review_id",
            "dataset_id",
            "provider",
            "price_basis",
            "limitations",
        ):
            _text(raw, field)
        date.fromisoformat(_text(raw, "reviewed_on"))
        if raw.get("data_kind") not in {"real", "synthetic"}:
            raise ValueError("Explicit benchmark data kind required.")
        if not isinstance(raw.get("adjustment_settings"), dict):
            raise ValueError("Benchmark adjustment settings required.")
        if raw.get("currency") != "USD" or raw.get("return_convention") != (
            "price_return"
        ):
            raise ValueError("Benchmark requires USD price returns.")
        if raw["provider"] == "yahoo" and (
            raw["price_basis"] != BASIS or raw["adjustment_settings"] != SETTINGS
        ):
            raise ValueError("Yahoo benchmark requires quote Close settings.")
        for key in ("basis_review", "benchmark"):
            value = raw.get(key)
            if not isinstance(value, dict):
                raise ValueError("Benchmark identity and basis decisions required.")
            Review._decision(value)
            _text(value, "description")
        mapping = raw["benchmark"]
        symbol = _text(mapping, "provider_symbol")
        if (
            symbol not in BENCHMARKS
            or mapping.get("instrument_type") != (BENCHMARKS[symbol])
        ):
            raise ValueError("Unsupported benchmark identity.")
        pins = mapping.get("sources")
        if not isinstance(pins, list) or (mapping["status"] == "approved" and not pins):
            raise ValueError("Approved benchmark requires pinned sources.")
        for pin in pins:
            _hash(pin, "source_sha256")
            _hash(pin, "metadata_sha256")
        return cls(json.loads(canonical_json(raw)))

    @property
    def sha256(self) -> str:
        return fingerprint(self.declarations)


def benchmark_history(
    session: Session,
    review: BenchmarkReview,
    day: date,
    *,
    history_start: date | None = None,
) -> list[Observation]:
    raw = review.declarations
    dataset = session.get(PriceDataset, raw["dataset_id"])
    if dataset is None:
        return []
    if dataset.data_kind != raw["data_kind"]:
        raise EvidenceUnavailableError("Benchmark data kind differs from review.")
    # Benchmark identity is reviewed independently of membership: an index/ETF
    # must never be fabricated as a constituent identity.
    return _observations(
        session,
        raw,
        raw["benchmark"],
        day,
        True,
        observation_limit=121,
        history_start=history_start,
    )


def reviewed_context(
    history: list[Observation],
    market: list[Observation],
    day: date,
    review: Review,
    benchmark: BenchmarkReview | None,
) -> dict[str, Any]:
    """Apply the same dated context policy in research and API evidence."""
    policy = context_policy(review.declarations)
    if benchmark is not None and (
        benchmark.declarations["data_kind"] != review.declarations["data_kind"]
    ):
        raise EvidenceUnavailableError("Benchmark data kind differs from review.")
    liquidity = policy.get("dollar_volume_review")
    return calculate_context(
        history,
        market,
        day,
        dollar_volume_approved=liquidity is not None,
        dollar_volume_dates={
            r.day
            for r in history
            if liquidity is not None
            and not decision_blockers(liquidity, r.day, "dollar_volume")
        },
        comparison_approved=bool(policy) and benchmark is not None,
    )
