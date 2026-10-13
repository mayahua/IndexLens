"""Source evidence and derived history; identities are source-local pairs."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source", "dataset_sha256", "dataset_kind"),
        CheckConstraint("dataset_kind IN ('real', 'synthetic')"),
        CheckConstraint("dataset_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("coverage_start <= coverage_end"),
        CheckConstraint("record_count > 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    dataset_sha256: Mapped[str] = mapped_column(Text)
    dataset_kind: Mapped[str] = mapped_column(Text)
    coverage_start: Mapped[date]
    coverage_end: Mapped[date]
    record_count: Mapped[int]
    source_columns: Mapped[list[str]] = mapped_column(JSONB)
    frame_dtypes: Mapped[dict[str, str]] = mapped_column(JSONB)
    source_bytes: Mapped[bytes] = mapped_column(LargeBinary)


class SourceIdentity(Base):
    __tablename__ = "source_identities"
    __table_args__ = (UniqueConstraint("version_id", "symbol", "cik"),)

    version_id: Mapped[int] = mapped_column(
        ForeignKey("source_versions.id"), primary_key=True
    )
    identity_position: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(Text)
    cik: Mapped[str] = mapped_column(Text)


class SourceRow(Base):
    __tablename__ = "source_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "identity_position"],
            ["source_identities.version_id", "source_identities.identity_position"],
        ),
        CheckConstraint("source_record_position >= 0"),
        CheckConstraint("record_sha256 ~ '^[0-9a-f]{64}$'"),
        Index("ix_source_rows_identity", "version_id", "identity_position"),
    )

    version_id: Mapped[int] = mapped_column(primary_key=True)
    source_record_position: Mapped[int] = mapped_column(primary_key=True)
    identity_position: Mapped[int]
    record_sha256: Mapped[str] = mapped_column(Text)
    original_fields: Mapped[dict[str, str]] = mapped_column(JSONB)
    audited_fields: Mapped[dict[str, str | None]] = mapped_column(JSONB)


class MembershipInterval(Base):
    __tablename__ = "membership_intervals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "source_record_position"],
            ["source_rows.version_id", "source_rows.source_record_position"],
        ),
        CheckConstraint("date_removed IS NULL OR date_removed >= date_added"),
        Index("ix_membership_intervals_date", "version_id", "date_added"),
    )

    version_id: Mapped[int] = mapped_column(primary_key=True)
    source_record_position: Mapped[int] = mapped_column(primary_key=True)
    date_added: Mapped[date]
    date_removed: Mapped[date | None]
    created_at: Mapped[date]
    date_added_approximate: Mapped[bool] = mapped_column(Boolean)
    date_removed_approximate: Mapped[bool] = mapped_column(Boolean)


class SourceEvent(Base):
    __tablename__ = "source_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "source_record_position"],
            [
                "membership_intervals.version_id",
                "membership_intervals.source_record_position",
            ],
        ),
        CheckConstraint("event_type IN ('ADD', 'REMOVE')"),
        Index("ix_source_events_date", "version_id", "event_date"),
    )

    version_id: Mapped[int] = mapped_column(primary_key=True)
    source_record_position: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, primary_key=True)
    event_date: Mapped[date]
    event_date_approximate: Mapped[bool]
    created_after_event: Mapped[bool]


class SourceIssue(Base):
    __tablename__ = "source_issues"

    version_id: Mapped[int] = mapped_column(
        ForeignKey("source_versions.id"), primary_key=True
    )
    issue_position: Mapped[int] = mapped_column(primary_key=True)
    issue_type: Mapped[str] = mapped_column(Text)
    issue_details: Mapped[str] = mapped_column(Text)
    column: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)


class PriceDataset(Base):
    __tablename__ = "price_datasets"
    __table_args__ = (CheckConstraint("data_kind IN ('real', 'synthetic')"),)

    dataset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data_kind: Mapped[str] = mapped_column(Text)


class PriceSource(Base):
    __tablename__ = "price_sources"
    __table_args__ = (
        UniqueConstraint("dataset_id", "source_sha256", "metadata_sha256"),
        UniqueConstraint("id", "dataset_id"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("metadata_sha256 ~ '^[0-9a-f]{64}$'"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("price_datasets.dataset_id"))
    provider: Mapped[str] = mapped_column(Text)
    price_basis: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_reference: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(Text)
    metadata_sha256: Mapped[str] = mapped_column(Text)
    source_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    declarations: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_counts: Mapped[dict[str, int]] = mapped_column(JSONB)


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "provider",
            "provider_symbol",
            "observation_date",
            "price_basis",
        ),
        UniqueConstraint("id", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("price_datasets.dataset_id"))
    provider: Mapped[str] = mapped_column(Text)
    provider_symbol: Mapped[str] = mapped_column(Text)
    observation_date: Mapped[date]
    price_basis: Mapped[str] = mapped_column(Text)
    # Exact decimal strings avoid precision loss; this is the first candidate,
    # never a selected winner when conflicted. All candidates live in price_rows.
    normalized_values: Mapped[dict[str, str | None]] = mapped_column(JSONB)
    conflicted: Mapped[bool] = mapped_column(Boolean)
    prices_usable: Mapped[bool] = mapped_column(Boolean)
    volume_usable: Mapped[bool] = mapped_column(Boolean)


class PriceRow(Base):
    __tablename__ = "price_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "dataset_id"],
            ["price_sources.id", "price_sources.dataset_id"],
        ),
        ForeignKeyConstraint(
            ["observation_id", "dataset_id"],
            ["price_observations.id", "price_observations.dataset_id"],
        ),
        CheckConstraint("record_position >= 0"),
        CheckConstraint("record_sha256 ~ '^[0-9a-f]{64}$'"),
        Index("ix_price_rows_observation", "observation_id"),
    )

    source_id: Mapped[int] = mapped_column(primary_key=True)
    record_position: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[str] = mapped_column(Text)
    observation_id: Mapped[int | None]
    record_sha256: Mapped[str] = mapped_column(Text)
    original_fields: Mapped[dict[str, str]] = mapped_column(JSONB)
    normalized_values: Mapped[dict[str, str | None]] = mapped_column(JSONB)
    issues: Mapped[list[str]] = mapped_column(JSONB)
