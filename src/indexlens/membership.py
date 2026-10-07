"""Reconstruct membership snapshots and events from audited source history."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date

import pandas as pd

from indexlens.data_audit import AuditResult, ValidationIssue


@dataclass
class PreparedHistory:
    """Audited rows with source metadata, coverage, and preserved issues."""

    data: pd.DataFrame
    source: str
    dataset_sha256: str
    coverage_start: date
    coverage_end: date
    issues: list[ValidationIssue]


@dataclass
class MembershipSnapshot:
    """Selected rows with provenance, source audit issues, and dated warnings."""

    data: pd.DataFrame
    as_of: date
    source: str
    dataset_sha256: str
    coverage_start: date
    coverage_end: date
    issues: list[ValidationIssue]


@dataclass
class MembershipDifference:
    """Net source-identity changes, with independent endpoint evidence.

    Identity sets are unordered; sort them when presenting results. Retained
    snapshots hold the dates, rows, provenance, flags, and source-wide issues.
    """

    earlier: MembershipSnapshot
    later: MembershipSnapshot
    appeared: set[tuple[str, str]]
    disappeared: set[tuple[str, str]]
    unchanged: set[tuple[str, str]]


@dataclass
class SourceEventTable:
    """Event rows with independent source evidence and version metadata.

    Match events to source_data by source_record_position within dataset_sha256.
    Issues are source-wide audit findings; individual event flags live in data.
    """

    data: pd.DataFrame
    source_data: pd.DataFrame
    source: str
    dataset_sha256: str
    coverage_start: date
    coverage_end: date
    issues: list[ValidationIssue]


def prepare_history(
    audit_result: AuditResult,
    *,
    source: str,
    dataset_sha256: str,
    coverage_start: date,
    coverage_end: date,
) -> PreparedHistory:
    """Copy a valid audit and assign zero-based source record positions.

    Pass a fresh audit in original source order, before sorting or filtering.
    The caller provides the matching file hash and the verified coverage.
    Output column source_record_position. Preserve warnings and all rows, 
    including those outside the query coverage. Raise ValueError for a failed audit 
    or reversed coverage.
    """
    if not audit_result.report.is_valid:
        raise ValueError("Cannot prepare membership history from an invalid audit.")

    if coverage_start > coverage_end:
        raise ValueError("Coverage start must not be later than coverage end.")

    data = audit_result.data.copy(deep=True)
    issues = deepcopy(audit_result.report.issues)
    data["source_record_position"] = range(len(data))

    return PreparedHistory(
        data=data,
        source=source,
        dataset_sha256=dataset_sha256,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        issues=issues,
    )


def membership_at(history: PreparedHistory, as_of: date) -> MembershipSnapshot:
    """Select nominal membership for a date within the reviewed coverage.

    Include addition dates, exclude removal dates, and require created_at to
    be on or before the requested date. Approximate dates retain their flags.
    Preserve source row order, index labels, and record positions. Copy the
    selected rows and source-wide issues. Flag repeated active identities and
    symbols with multiple active CIKs without dropping rows.
    Raise ValueError for a date outside the inclusive coverage range.
    """
    if not history.coverage_start <= as_of <= history.coverage_end:
        raise ValueError("Requested date is outside the supported coverage.")

    data = history.data
    query_date = pd.Timestamp(as_of)

    added_by = data["date_added_parsed"] <= query_date
    not_removed_by = data["date_removed_parsed"].isna() | (
        data["date_removed_parsed"] > query_date
    )
    created_by = data["created_at_parsed"] <= query_date

    membership_mask = added_by & not_removed_by & created_by
    members = data.loc[membership_mask].copy()
    issues = deepcopy(history.issues)

    repeated_identity = members.duplicated(["symbol", "cik"], keep=False)
    if repeated_identity.any():
        issues.append(
            ValidationIssue(
                "duplicate_active_identity",
                f"Multiple source rows represent the same (symbol, cik) on {as_of}.",
                row_count=int(repeated_identity.sum()),
            )
        )

    multiple_ciks = members.groupby("symbol")["cik"].transform("nunique") > 1
    if multiple_ciks.any():
        issues.append(
            ValidationIssue(
                "multiple_active_ciks",
                f"A symbol has multiple active CIKs on {as_of}.",
                column="symbol",
                row_count=int(multiple_ciks.sum()),
            )
        )

    return MembershipSnapshot(
        data=members,
        as_of=as_of,
        source=history.source,
        dataset_sha256=history.dataset_sha256,
        coverage_start=history.coverage_start,
        coverage_end=history.coverage_end,
        issues=issues,
    )


def membership_keys(snapshot: MembershipSnapshot) -> set[tuple[str, str]]:
    """Return distinct (symbol, cik) source identities without changing rows.

    These pairs identify source values, not permanent real-world securities.
    Repeated pairs count once in the set; all evidence stays in snapshot.data.
    """
    keys: set[tuple[str, str]] = set()
    for symbol, cik in snapshot.data[["symbol", "cik"]].itertuples(
        index=False, name=None
    ):
        keys.add((symbol, cik))
    return keys


def compare_snapshots(
    earlier: MembershipSnapshot, later: MembershipSnapshot
) -> MembershipDifference:
    """Compare endpoint identities from one source version and coverage range.

    Equal dates are allowed. Reject reversed dates or mismatched source context.
    Unchanged means present at both endpoints, not continuous membership; these
    net differences do not recover intervening removals, re-entry, or event dates.
    """
    if earlier.as_of > later.as_of:
        raise ValueError("Earlier snapshot date must not be after later snapshot date.")

    earlier_context = (
        earlier.source,
        earlier.dataset_sha256,
        earlier.coverage_start,
        earlier.coverage_end,
    )
    later_context = (
        later.source,
        later.dataset_sha256,
        later.coverage_start,
        later.coverage_end,
    )
    if earlier_context != later_context:
        raise ValueError(
            "Snapshots must use the same source, dataset version, and coverage."
        )

    earlier_keys = membership_keys(earlier)
    later_keys = membership_keys(later)

    appeared = later_keys - earlier_keys
    disappeared = earlier_keys - later_keys
    unchanged = earlier_keys & later_keys

    return MembershipDifference(
        earlier=deepcopy(earlier),
        later=deepcopy(later),
        appeared=appeared,
        disappeared=disappeared,
        unchanged=unchanged,
    )


def _addition_events(history: PreparedHistory) -> pd.DataFrame:
    """Return ADD rows in (coverage_start, coverage_end].

    Keep nominal dates, addition-specific flags, created_at, and record positions.
    Source metadata and complete original rows remain in history.
    """
    data = history.data
    coverage_start = pd.Timestamp(history.coverage_start)
    coverage_end = pd.Timestamp(history.coverage_end)

    added_in_coverage = (data["date_added_parsed"] > coverage_start) & (
        data["date_added_parsed"] <= coverage_end
    )
    additions = data.loc[
        added_in_coverage,
        [
            "symbol",
            "cik",
            "date_added_parsed",
            "date_added_approximate",
            "created_at_parsed",
            "source_record_position",
        ],
    ].copy()
    additions = additions.rename(
        columns={
            "date_added_parsed": "event_date",
            "date_added_approximate": "event_date_approximate",
        }
    )
    additions["event_type"] = "ADD"
    return additions


def _removal_events(history: PreparedHistory) -> pd.DataFrame:
    """Return recorded REMOVE rows in (coverage_start, coverage_end].

    Keep nominal dates, removal-specific flags, created_at, and record positions.
    Source metadata and complete original rows remain in history.
    """
    data = history.data
    coverage_start = pd.Timestamp(history.coverage_start)
    coverage_end = pd.Timestamp(history.coverage_end)

    removed_in_coverage = (
        data["date_removed_parsed"].notna()
        & (data["date_removed_parsed"] > coverage_start)
        & (data["date_removed_parsed"] <= coverage_end)
    )
    removals = data.loc[
        removed_in_coverage,
        [
            "symbol",
            "cik",
            "date_removed_parsed",
            "date_removed_approximate",
            "created_at_parsed",
            "source_record_position",
        ],
    ].copy()
    removals = removals.rename(
        columns={
            "date_removed_parsed": "event_date",
            "date_removed_approximate": "event_date_approximate",
        }
    )
    removals["event_type"] = "REMOVE"
    return removals


def _event_rows(history: PreparedHistory) -> pd.DataFrame:
    """Combine source events in a reproducible order, retaining every record.

    Same-date ordering is for presentation, not an intraday sequence. Reset
    display index labels while preserving source_record_position values.
    Flag created_at later than each nominal event date without rewriting dates;
    this comparison does not establish historical information availability.
    Source metadata and complete original rows remain in history.
    """
    additions = _addition_events(history)
    removals = _removal_events(history)
    events = pd.concat([additions, removals], ignore_index=True)
    events = events.sort_values(
        ["event_date", "symbol", "cik", "event_type", "source_record_position"],
        ignore_index=True,
    )
    events["created_after_event"] = events["created_at_parsed"] > events["event_date"]
    return events


def build_source_events(history: PreparedHistory) -> SourceEventTable:
    """Return recorded events in (coverage_start, coverage_end] with evidence.

    Keep nominal dates and event-specific flags, including both events for equal
    endpoints. Copy all audited source rows and source-wide issues, even for no events.
    Resolve source_record_position in source_data by column value, not index label.
    """
    return SourceEventTable(
        data=_event_rows(history),
        source_data=history.data.copy(deep=True),
        source=history.source,
        dataset_sha256=history.dataset_sha256,
        coverage_start=history.coverage_start,
        coverage_end=history.coverage_end,
        issues=deepcopy(history.issues),
    )
