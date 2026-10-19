"""Read stored source records and operator-selected research reports."""

import json
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, defer

from indexlens.db.models import (
    MembershipInterval,
    PriceObservation,
    SourceIdentity,
    SourceRow,
    SourceVersion,
)
from indexlens.db.repository import membership_at_db, source_events_db
from indexlens.features.query import query_features, reviewed_history
from indexlens.features.review import Review


def read_json(
    path: Path, maximum: int = 64 * 1024 * 1024
) -> tuple[dict[str, Any], str]:
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Configured JSON file exceeds the size limit.")
    raw = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError("Configured JSON must contain an object.")
    return raw, sha256(content).hexdigest()


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def ready(self) -> None:
        self.session.execute(select(SourceVersion.id).limit(1))
        self.session.execute(text("SELECT 1 FROM price_observations LIMIT 1"))

    def version(self, version_id: int) -> SourceVersion | None:
        return self.session.scalar(
            select(SourceVersion)
            .options(defer(SourceVersion.source_bytes))
            .where(SourceVersion.id == version_id)
        )

    def identities(self, version_id: int) -> list[SourceIdentity]:
        return list(
            self.session.scalars(
                select(SourceIdentity)
                .where(SourceIdentity.version_id == version_id)
                .order_by(SourceIdentity.identity_position)
            )
        )

    def identity(self, version_id: int, position: int) -> SourceIdentity | None:
        return self.session.get(SourceIdentity, (version_id, position))

    def records(self, version_id: int) -> dict[int, dict[str, Any]]:
        rows = self.session.execute(
            select(SourceRow, MembershipInterval)
            .join(MembershipInterval)
            .where(SourceRow.version_id == version_id)
            .order_by(SourceRow.source_record_position)
        )
        return {
            row.source_record_position: dict(
                security_id=f"v{version_id}-s{row.identity_position}",
                source_record_position=row.source_record_position,
                record_sha256=row.record_sha256,
                original_fields=row.original_fields,
                date_added=str(interval.date_added),
                date_removed=str(interval.date_removed)
                if interval.date_removed
                else None,
                created_at=str(interval.created_at),
                date_added_approximate=interval.date_added_approximate,
                date_removed_approximate=interval.date_removed_approximate,
            )
            for row, interval in rows
        }

    membership = staticmethod(membership_at_db)
    events = staticmethod(source_events_db)
    features = staticmethod(query_features)
    history = staticmethod(reviewed_history)

    def price_values(
        self, review: Review, symbol: str, start: date, end: date
    ) -> dict[str, dict[str, Any]]:
        raw = review.declarations
        return {
            str(row.observation_date): dict(
                observation_id=row.id,
                values=row.normalized_values,
                conflicted=row.conflicted,
                prices_usable=row.prices_usable,
                volume_usable=row.volume_usable,
            )
            for row in self.session.scalars(
                select(PriceObservation)
                .where(
                    PriceObservation.dataset_id == raw["dataset_id"],
                    PriceObservation.provider == raw["provider"],
                    PriceObservation.price_basis == raw["price_basis"],
                    PriceObservation.provider_symbol == symbol,
                    PriceObservation.observation_date.between(start, end),
                )
                .order_by(PriceObservation.observation_date)
            )
        }


def issues(values: Any) -> list[dict[str, Any]]:
    return [asdict(issue) for issue in values]
