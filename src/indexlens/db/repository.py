"""Source-history queries backed by PostgreSQL; sessions belong to callers."""

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from indexlens.data_audit import ValidationIssue
from indexlens.membership import MembershipSnapshot, SourceEventTable, _snapshot_issues

from .models import (
    MembershipInterval,
    SourceEvent,
    SourceIdentity,
    SourceIssue,
    SourceRow,
    SourceVersion,
)


def source_version(session: Session, version_id: int) -> SourceVersion:
    version = session.get(SourceVersion, version_id)
    if version is None:
        raise ValueError("Unknown source version.")
    return version


def _issues(session: Session, version_id: int) -> list[ValidationIssue]:
    return [
        ValidationIssue(row.issue_type, row.issue_details, row.column, row.row_count)
        for row in session.scalars(
            select(SourceIssue)
            .where(SourceIssue.version_id == version_id)
            .order_by(SourceIssue.issue_position)
        )
    ]


def _frame(
    rows: Sequence[tuple[SourceRow, MembershipInterval]], version: SourceVersion
) -> pd.DataFrame:
    frame = pd.DataFrame(
        [row.audited_fields for row, _ in rows],
        columns=version.source_columns,
        dtype="string",
    )
    date_values = {
        "date_added": [interval.date_added for _, interval in rows],
        "date_removed": [interval.date_removed for _, interval in rows],
        "created_at": [interval.created_at for _, interval in rows],
    }
    for column, values in date_values.items():
        parsed = f"{column}_parsed"
        frame[parsed] = pd.Series(values, dtype=version.frame_dtypes[parsed])
        if column != "created_at":
            flags = [getattr(interval, f"{column}_approximate") for _, interval in rows]
            frame[f"{column}_approximate"] = pd.Series(flags, dtype="boolean")
    positions = [row.source_record_position for row, _ in rows]
    frame["source_record_position"] = pd.Series(positions, dtype="int64")
    frame.index = pd.Index(positions, dtype="int64")
    return frame.astype(version.frame_dtypes)


def membership_at_db(
    session: Session, version_id: int, as_of: date
) -> MembershipSnapshot:
    """Select guarded intervals in SQL, retaining order and ambiguity findings."""
    version = source_version(session, version_id)
    if not version.coverage_start <= as_of <= version.coverage_end:
        raise ValueError("Requested date is outside the supported coverage.")
    rows = (
        session.execute(
            select(SourceRow, MembershipInterval)
            .join(MembershipInterval)
            .where(
                SourceRow.version_id == version_id,
                MembershipInterval.date_added <= as_of,
                (MembershipInterval.date_removed.is_(None))
                | (MembershipInterval.date_removed > as_of),
                MembershipInterval.created_at <= as_of,
            )
            .order_by(SourceRow.source_record_position)
        )
        .tuples()
        .all()
    )
    members = _frame(rows, version)
    issues = _snapshot_issues(members, as_of, _issues(session, version_id))
    return MembershipSnapshot(
        members,
        as_of,
        version.source,
        version.dataset_sha256,
        version.coverage_start,
        version.coverage_end,
        issues,
    )


def source_events_db(session: Session, version_id: int) -> SourceEventTable:
    """Read stored nominal events and complete original source evidence."""
    version = source_version(session, version_id)
    records = session.execute(
        select(
            SourceIdentity.symbol,
            SourceIdentity.cik,
            SourceEvent.event_date,
            SourceEvent.event_date_approximate,
            MembershipInterval.created_at.label("created_at_parsed"),
            SourceEvent.source_record_position,
            SourceEvent.event_type,
            SourceEvent.created_after_event,
        )
        .select_from(SourceEvent)
        .join(MembershipInterval)
        .join(SourceRow)
        .join(SourceIdentity)
        .where(SourceEvent.version_id == version_id)
        .order_by(
            SourceEvent.event_date,
            SourceIdentity.symbol.collate("C"),
            SourceIdentity.cik.collate("C"),
            SourceEvent.event_type.collate("C"),
            SourceEvent.source_record_position,
        )
    ).all()
    columns = [
        "symbol",
        "cik",
        "event_date",
        "event_date_approximate",
        "created_at_parsed",
        "source_record_position",
        "event_type",
        "created_after_event",
    ]
    frame = pd.DataFrame.from_records(
        [tuple(row) for row in records], columns=columns
    ).astype(
        {
            "symbol": "string",
            "cik": "string",
            "event_date_approximate": "boolean",
            "source_record_position": "int64",
            "event_type": pd.Series(["ADD"]).dtype,
            "created_after_event": "bool",
        }
    )
    for column in ("event_date", "created_at_parsed"):
        source_column = "date_added_parsed" if column == "event_date" else column
        date_dtype = version.frame_dtypes[source_column]
        frame[column] = pd.to_datetime(frame[column]).astype(np.dtype(date_dtype))
    rows = (
        session.execute(
            select(SourceRow, MembershipInterval)
            .join(MembershipInterval)
            .where(SourceRow.version_id == version_id)
            .order_by(SourceRow.source_record_position)
        )
        .tuples()
        .all()
    )
    return SourceEventTable(
        frame,
        _frame(rows, version),
        version.source,
        version.dataset_sha256,
        version.coverage_start,
        version.coverage_end,
        _issues(session, version_id),
    )
