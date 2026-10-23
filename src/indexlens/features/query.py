"""Read features from stored price evidence with an explicit review manifest."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from indexlens.db.models import (
    PriceDataset,
    PriceObservation,
    PriceRow,
    PriceSource,
    SourceRow,
    SourceVersion,
)
from indexlens.db.repository import membership_at_db
from indexlens.prices.normalization import has_invalid_prices

from .core import FEATURE_VERSION, Observation, calculate, momentum_order
from .review import Review, decision_blockers


class EvidenceUnavailableError(ValueError):
    """Stored datasets or source versions do not match the review."""


class MembershipContextError(ValueError):
    """The requested ranking lacks a common observation date."""


def _version(session: Session, review: dict[str, Any]) -> SourceVersion:
    versions = session.scalars(
        select(SourceVersion).where(
            SourceVersion.dataset_sha256 == review["membership_sha256"],
            SourceVersion.dataset_kind == review["data_kind"],
        )
    ).all()
    if len(versions) != 1:
        raise EvidenceUnavailableError(
            "Review must identify exactly one membership source version."
        )
    return versions[0]


def _identity_valid(
    session: Session, version: SourceVersion, mapping: dict[str, Any]
) -> bool:
    for position in mapping["source_record_positions"]:
        row = session.get(SourceRow, (version.id, position))
        if row is None or any(
            row.original_fields.get(key) != mapping[key] for key in ("symbol", "cik")
        ):
            return False
    return True


def _observations(
    session: Session,
    review: dict[str, Any],
    mapping: dict[str, Any],
    day: date,
    identity_valid: bool,
    *,
    history_start: date | None = None,
    observation_limit: int = 21,
) -> list[Observation]:
    # Limit by chronology, never by numeric or review eligibility.
    statement = (
        select(PriceObservation)
        .where(
            PriceObservation.dataset_id == review["dataset_id"],
            PriceObservation.provider == review["provider"],
            PriceObservation.provider_symbol == mapping["provider_symbol"],
            PriceObservation.price_basis == review["price_basis"],
            PriceObservation.observation_date <= day,
        )
        .order_by(PriceObservation.observation_date.desc())
    )
    if history_start is None:
        statement = statement.limit(observation_limit)
    else:
        statement = statement.where(PriceObservation.observation_date >= history_start)
    observations = session.scalars(statement).all()
    if not observations:
        return []
    evidence_by_observation: dict[int, list[tuple[PriceRow, PriceSource]]] = {
        observation.id: [] for observation in observations
    }
    if history_start is None:
        evidence_rows = session.execute(
            select(PriceRow, PriceSource)
            .join(PriceSource, PriceRow.source_id == PriceSource.id)
            .where(PriceRow.observation_id.in_(evidence_by_observation))
            .order_by(
                PriceRow.observation_id, PriceRow.source_id, PriceRow.record_position
            )
        )
        pairs = [(row, source) for row, source in evidence_rows]
    else:
        # Histories reuse each source once, including its potentially large CSV bytes.
        price_rows = session.scalars(
            select(PriceRow)
            .where(PriceRow.observation_id.in_(evidence_by_observation))
            .order_by(
                PriceRow.observation_id, PriceRow.source_id, PriceRow.record_position
            )
        ).all()
        sources = {
            source.id: source
            for source in session.scalars(
                select(PriceSource).where(
                    PriceSource.id.in_({r.source_id for r in price_rows})
                )
            )
        }
        pairs = [(row, sources[row.source_id]) for row in price_rows]
    for row, source in pairs:
        if row.observation_id is not None:
            evidence_by_observation[row.observation_id].append((row, source))
    pins = {
        (source["source_sha256"], source["metadata_sha256"])
        for source in mapping["sources"]
    }
    result = []
    for observation in observations:
        blockers = decision_blockers(mapping, observation.observation_date, "mapping")
        blockers += decision_blockers(
            review["basis_review"], observation.observation_date, "basis"
        )
        if not identity_valid:
            blockers.append("source_identity_mismatch")
        if observation.conflicted:
            blockers.append("conflicting_observation")
        evidence = evidence_by_observation[observation.id]
        provenance = []
        if not evidence:
            blockers.append("missing_source_evidence")
        for row, source in evidence:
            if (source.source_sha256, source.metadata_sha256) not in pins:
                blockers.append("unreviewed_source")
            if (
                source.declarations.get("adjustment_settings")
                != review["adjustment_settings"]
            ):
                blockers.append("adjustment_settings_mismatch")
            provenance.append(
                dict(
                    source_id=source.id,
                    source_sha256=source.source_sha256,
                    metadata_sha256=source.metadata_sha256,
                    record_position=row.record_position,
                    record_sha256=row.record_sha256,
                    retrieved_at=source.retrieved_at.isoformat(),
                    source_reference=source.declarations["source_reference"],
                    raw_evidence=source.declarations.get("raw_evidence", []),
                    issues=row.issues,
                )
            )
        close = observation.normalized_values["close"]
        volume = observation.normalized_values["volume"]
        # Missing open/high/low does not invalidate a valid close. Invalid prices
        # and conflicts still block calculations; stored flags remain unchanged.
        close_usable = (
            close is not None
            and not observation.conflicted
            and bool(evidence)
            and not any(has_invalid_prices(row.issues) for row, _ in evidence)
        )
        result.append(
            Observation(
                observation.observation_date,
                Decimal(close) if close is not None else None,
                Decimal(volume) if volume is not None else None,
                close_usable,
                observation.volume_usable,
                tuple(sorted(set(blockers))),
                tuple(provenance),
            )
        )
    return result


def query_features(
    session: Session, review: Review, day: date, *, membership: bool = False
) -> dict[str, Any]:
    """Return every declared identity. Caller owns a consistent read transaction.

    Ranks require an observed reference date shared by the entire universe. A
    common date does not make unavailable windows eligible for a rank.
    """
    raw = review.declarations
    dataset = session.get(PriceDataset, raw["dataset_id"])
    if dataset is None or dataset.data_kind != raw["data_kind"]:
        raise EvidenceUnavailableError(
            "Dataset missing or real/synthetic declaration mismatch."
        )
    version = _version(session, raw)
    mappings = raw["universe"]
    results = {}
    for mapping in mappings:
        identity = (mapping["symbol"], mapping["cik"])
        observations = _observations(
            session, raw, mapping, day, _identity_valid(session, version, mapping)
        )
        results[identity] = calculate(observations, day)
    common = all(
        result.inputs and result.inputs[-1]["observation_date"] == day.isoformat()
        for result in results.values()
    )
    order = momentum_order(results) if common else []
    ranks = {identity: position + 1 for position, identity in enumerate(order)}
    rows = []
    for identity, result in results.items():
        rows.append(
            dict(
                symbol=identity[0],
                cik=identity[1],
                rank=ranks.get(identity),
                rank_unavailable=(
                    ["no_common_observation_date"]
                    if not common
                    else result.unavailable.get("return_20", [])
                ),
                features={
                    key: str(value) if value is not None else None
                    for key, value in result.values.items()
                },
                unavailable=result.unavailable,
                windows=result.windows,
                inputs=result.inputs,
            )
        )
    unavailable_position = len(rows) + 1
    rows.sort(
        key=lambda row: (row["rank"] or unavailable_position, row["symbol"], row["cik"])
    )
    context = None
    if membership:
        if not common:
            raise MembershipContextError(
                "Membership context requires a common observation date."
            )
        snapshot = membership_at_db(session, version.id, day)
        active = {
            (str(row.symbol), str(row.cik))
            for row in snapshot.data.itertuples(index=False)
        }
        context = dict(
            reference_date=day.isoformat(),
            source_version_id=version.id,
            source_sha256=version.dataset_sha256,
            coverage_start=version.coverage_start.isoformat(),
            coverage_end=version.coverage_end.isoformat(),
            issues=[asdict(issue) for issue in snapshot.issues],
        )
        for row in rows:
            identity = (row["symbol"], row["cik"])
            # An unresolved mapping must not imply a joined membership identity.
            approved = not any(
                blocker
                in {
                    "unresolved_mapping",
                    "outside_mapping_dates",
                    "source_identity_mismatch",
                }
                for observation in results[identity].inputs
                for blocker in observation["blockers"]
            )
            row["member_at_reference"] = identity in active if approved else None
    return dict(
        description=(
            "Descriptive momentum ranking; not an inclusion/removal probability."
        ),
        feature_version=FEATURE_VERSION,
        dataset_id=raw["dataset_id"],
        data_kind=raw["data_kind"],
        provider=raw["provider"],
        price_basis=raw["price_basis"],
        basis_review=raw["basis_review"],
        adjustment_settings=raw["adjustment_settings"],
        observation_date=day.isoformat(),
        common_observation_date=common,
        ranking_complete=len(order) == len(mappings),
        universe=[dict(symbol=m["symbol"], cik=m["cik"]) for m in mappings],
        review_sha256=review.sha256,
        review=raw,
        membership_context=context,
        results=rows,
    )


def reviewed_history(
    session: Session,
    review: Review,
    mapping: dict[str, Any],
    start: date | None,
    end: date,
    *,
    version: SourceVersion | None = None,
    observation_limit: int = 21,
) -> list[Observation]:
    """Read an inclusive history using the same evidence gates as feature queries.

    A null start selects at most observation_limit positions, defaulting to 21.
    Callers select chronological windows and supply the required session to calculate.
    A membership version already resolved in this session can be reused.
    """
    raw = review.declarations
    if not 1 <= observation_limit <= 121:
        raise ValueError("Observation limit must be between 1 and 121.")
    dataset = session.get(PriceDataset, raw["dataset_id"])
    if dataset is None or dataset.data_kind != raw["data_kind"]:
        raise EvidenceUnavailableError(
            "Dataset missing or real/synthetic declaration mismatch."
        )
    if version is None:
        version = _version(session, raw)
    elif (version.dataset_sha256, version.dataset_kind) != (
        raw["membership_sha256"],
        raw["data_kind"],
    ):
        raise EvidenceUnavailableError("Membership version does not match the review.")
    return sorted(
        _observations(
            session,
            raw,
            mapping,
            end,
            _identity_valid(session, version, mapping),
            history_start=start,
            observation_limit=observation_limit,
        ),
        key=lambda row: row.day,
    )
