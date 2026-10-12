"""Atomic, repeatable loads of a pinned local membership CSV."""

import csv
import json
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pandas as pd
from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from indexlens.data_audit import audit_membership, load_membership_csv
from indexlens.membership import PreparedHistory, build_source_events, prepare_history

from .models import (
    MembershipInterval,
    SourceEvent,
    SourceIdentity,
    SourceIssue,
    SourceRow,
    SourceVersion,
)


@dataclass(frozen=True)
class LoadResult:
    version_id: int
    inserted: bool
    rows: int
    events: int
    dataset_sha256: str


def _record_sha256(fields: dict[str, str]) -> str:
    """Hash original fields using the canonical stored-record representation."""
    return sha256(
        json.dumps(
            fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def read_source(
    path: Path, *, source: str, coverage_start: date, coverage_end: date
) -> tuple[bytes, PreparedHistory, list[dict[str, str]]]:
    """Audit exactly the bytes hashed, using the existing CSV reader."""
    payload = path.read_bytes()
    with TemporaryDirectory(prefix="indexlens-source-") as temporary:
        pinned = Path(temporary) / "source.csv"
        pinned.write_bytes(payload)
        audit = audit_membership(load_membership_csv(pinned))
    history = prepare_history(
        audit,
        source=source,
        dataset_sha256=sha256(payload).hexdigest(),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    reader = csv.reader(StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    columns = next(reader)
    original = [
        dict(zip(columns, row, strict=True))
        for row in reader
        if row and not (len(row) == 1 and not row[0].strip())
    ]
    if len(original) != len(history.data):
        raise ValueError("Original and audited source record counts differ.")
    return payload, history, original


def load_source_csv(
    engine: Engine,
    path: Path,
    *,
    source: str,
    dataset_kind: Literal["real", "synthetic"],
    coverage_start: date,
    coverage_end: date,
) -> LoadResult:
    """Commit the entire source version, or roll it back on any failure.

    Repeated or concurrent loads return the same version. Reusing that version
    with different coverage raises ValueError. This function owns its session.
    """
    if not source.strip() or dataset_kind not in {"real", "synthetic"}:
        raise ValueError("Supply a source label and real or synthetic dataset kind.")
    payload, history, original = read_source(
        path, source=source, coverage_start=coverage_start, coverage_end=coverage_end
    )
    events = build_source_events(history).data
    columns = list(original[0])
    with Session(engine) as session, session.begin():
        version_id = session.scalar(
            pg_insert(SourceVersion)
            .values(
                source=source,
                dataset_sha256=history.dataset_sha256,
                dataset_kind=dataset_kind,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                record_count=len(original),
                source_columns=columns,
                frame_dtypes={str(c): str(t) for c, t in history.data.dtypes.items()},
                source_bytes=payload,
            )
            .on_conflict_do_nothing(
                index_elements=["source", "dataset_sha256", "dataset_kind"]
            )
            .returning(SourceVersion.id)
        )
        if version_id is None:
            existing = session.scalars(
                select(SourceVersion).where(
                    SourceVersion.source == source,
                    SourceVersion.dataset_sha256 == history.dataset_sha256,
                    SourceVersion.dataset_kind == dataset_kind,
                )
            ).one()
            if (existing.coverage_start, existing.coverage_end) != (
                coverage_start,
                coverage_end,
            ):
                raise ValueError("Existing source version has different coverage.")
            return LoadResult(
                existing.id, False, len(original), len(events), history.dataset_sha256
            )

        identities: dict[tuple[str, str], int] = {}
        rows = []
        intervals = []
        for position, (_, row) in enumerate(history.data.iterrows()):
            key = (str(row["symbol"]), str(row["cik"]))
            identity_position = identities.setdefault(key, len(identities))
            record = original[position]
            record_digest = _record_sha256(record)
            rows.append(
                dict(
                    version_id=version_id,
                    source_record_position=position,
                    identity_position=identity_position,
                    record_sha256=record_digest,
                    original_fields=record,
                    audited_fields={
                        c: None if pd.isna(row[c]) else str(row[c]) for c in columns
                    },
                )
            )
            intervals.append(
                dict(
                    version_id=version_id,
                    source_record_position=position,
                    date_added=row["date_added_parsed"].date(),
                    date_removed=None
                    if pd.isna(row["date_removed_parsed"])
                    else row["date_removed_parsed"].date(),
                    created_at=row["created_at_parsed"].date(),
                    date_added_approximate=bool(row["date_added_approximate"]),
                    date_removed_approximate=bool(row["date_removed_approximate"]),
                )
            )
        session.execute(
            insert(SourceIdentity),
            [
                dict(
                    version_id=version_id,
                    identity_position=position,
                    symbol=symbol,
                    cik=cik,
                )
                for (symbol, cik), position in identities.items()
            ],
        )
        session.execute(insert(SourceRow), rows)
        session.execute(insert(MembershipInterval), intervals)
        if not events.empty:
            session.execute(
                insert(SourceEvent),
                [
                    dict(
                        version_id=version_id,
                        source_record_position=int(row["source_record_position"]),
                        event_type=row["event_type"],
                        event_date=row["event_date"].date(),
                        event_date_approximate=bool(row["event_date_approximate"]),
                        created_after_event=bool(row["created_after_event"]),
                    )
                    for _, row in events.iterrows()
                ],
            )
        if history.issues:
            session.execute(
                insert(SourceIssue),
                [
                    dict(
                        version_id=version_id,
                        issue_position=position,
                        issue_type=issue.issue_type,
                        issue_details=issue.issue_details,
                        column=issue.column,
                        row_count=issue.row_count,
                    )
                    for position, issue in enumerate(history.issues)
                ],
            )
        return LoadResult(
            version_id, True, len(original), len(events), history.dataset_sha256
        )
