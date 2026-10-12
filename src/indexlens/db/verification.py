"""Compare stored evidence and query results with the membership library."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from pandas.testing import assert_frame_equal
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from indexlens.membership import PreparedHistory, build_source_events, membership_at

from .loading import _record_sha256, read_source
from .models import SourceIdentity, SourceRow, SourceVersion
from .repository import membership_at_db, source_events_db, source_version


def _require_equal(actual: object, expected: object, description: str) -> None:
    if actual != expected:
        raise AssertionError(f"Stored source differs: {description}.")


def _verify_evidence(
    session: Session,
    version: SourceVersion,
    history: PreparedHistory,
    original: list[dict[str, str]],
) -> None:
    _require_equal(version.dataset_sha256, history.dataset_sha256, "file hash")
    _require_equal(version.record_count, len(original), "record count")
    columns = list(original[0])
    _require_equal(version.source_columns, columns, "source columns")
    _require_equal(
        version.frame_dtypes,
        {str(c): str(t) for c, t in history.data.dtypes.items()},
        "audited column types",
    )
    identities = {
        identity.identity_position: (identity.symbol, identity.cik)
        for identity in session.scalars(
            select(SourceIdentity).where(SourceIdentity.version_id == version.id)
        )
    }
    expected_pairs = set(
        history.data[["symbol", "cik"]].itertuples(index=False, name=None)
    )
    _require_equal(set(identities.values()), expected_pairs, "source identities")
    _require_equal(len(identities), len(expected_pairs), "identity count")
    rows = session.scalars(
        select(SourceRow)
        .where(SourceRow.version_id == version.id)
        .order_by(SourceRow.source_record_position)
    ).all()
    _require_equal(
        [row.source_record_position for row in rows],
        list(range(len(original))),
        "record positions",
    )
    for row, fields in zip(rows, original, strict=True):
        position = row.source_record_position
        audited = history.data.iloc[position]
        _require_equal(row.original_fields, fields, f"original fields at {position}")
        _require_equal(
            row.record_sha256, _record_sha256(fields), f"record hash at {position}"
        )
        _require_equal(
            row.audited_fields,
            {c: None if pd.isna(audited[c]) else str(audited[c]) for c in columns},
            f"audited fields at {position}",
        )
        _require_equal(
            identities.get(row.identity_position),
            (audited["symbol"], audited["cik"]),
            f"identity link at {position}",
        )


def verify_source(engine: Engine, version_id: int) -> int:
    """Check all stored evidence, events and snapshots around every change.

    Return the number of snapshot dates checked. Raise AssertionError on drift.
    Checks remain active under Python optimization. Source label, kind and
    reviewed coverage are declared metadata, not independently inferred facts.
    """
    with Session(engine) as session:
        version = source_version(session, version_id)
        with TemporaryDirectory(prefix="indexlens-verify-") as directory:
            path = Path(directory) / "source.csv"
            path.write_bytes(version.source_bytes)
            _, history, original = read_source(
                path,
                source=version.source,
                coverage_start=version.coverage_start,
                coverage_end=version.coverage_end,
            )
        _verify_evidence(session, version, history, original)
        expected_events = build_source_events(history)
        actual_events = source_events_db(session, version_id)
        assert_frame_equal(actual_events.data, expected_events.data)
        # Include stored intervals outside coverage and those without events.
        assert_frame_equal(actual_events.source_data, expected_events.source_data)
        _require_equal(actual_events.issues, expected_events.issues, "audit issues")
        _require_equal(
            (
                actual_events.source,
                actual_events.dataset_sha256,
                actual_events.coverage_start,
                actual_events.coverage_end,
            ),
            (
                history.source,
                history.dataset_sha256,
                history.coverage_start,
                history.coverage_end,
            ),
            "event metadata",
        )
        dates = {history.coverage_start, history.coverage_end}
        for column in ("date_added_parsed", "date_removed_parsed", "created_at_parsed"):
            for value in history.data[column].dropna().unique():
                for offset in (-1, 0, 1):
                    day = pd.Timestamp(value).date() + timedelta(days=offset)
                    if history.coverage_start <= day <= history.coverage_end:
                        dates.add(day)
        for day in sorted(dates):
            expected = membership_at(history, day)
            actual = membership_at_db(session, version_id, day)
            assert_frame_equal(actual.data, expected.data)
            _require_equal(actual.issues, expected.issues, f"snapshot issues on {day}")
            _require_equal(
                (
                    actual.as_of,
                    actual.source,
                    actual.dataset_sha256,
                    actual.coverage_start,
                    actual.coverage_end,
                ),
                (
                    expected.as_of,
                    expected.source,
                    expected.dataset_sha256,
                    expected.coverage_start,
                    expected.coverage_end,
                ),
                f"snapshot metadata on {day}",
            )
    return len(dates)
