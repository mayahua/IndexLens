"""Tests for history preparation, membership queries, comparisons, and source events."""

from copy import deepcopy
from datetime import date

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from indexlens.data_audit import ValidationIssue, audit_membership
from indexlens.membership import (
    PreparedHistory,
    _addition_events,
    _event_rows,
    _removal_events,
    build_source_events,
    compare_snapshots,
    membership_at,
    membership_keys,
    prepare_history,
)


@pytest.fixture
def membership_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["BETA", "ALFA"],
            "cik": ["0002", "0001"],
            "name": ["Beta Company", "Alpha Company"],
            "sector": ["industrials", "industrials"],
            "date_added": ["2026-01-08*", "2025-12-15"],
            "date_removed": ["2026-01-12*", None],
            "created_at": ["2026-01-08", "2026-01-01"],
        },
        dtype="string",
    )


def test_prepare_preserves_dates_flags_and_metadata(
    membership_data: pd.DataFrame,
) -> None:
    audit_result = audit_membership(membership_data)
    history = prepare_history(
        audit_result,
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    assert_frame_equal(history.data[audit_result.data.columns], audit_result.data)
    assert history.data["source_record_position"].tolist() == [0, 1]
    assert history.data.loc[0, "date_added"] == "2026-01-08*"
    assert history.data.loc[0, "date_added_parsed"] == pd.Timestamp("2026-01-08")
    assert history.data.loc[0, "date_added_approximate"]
    assert history.data.loc[0, "date_removed_parsed"] == pd.Timestamp("2026-01-12")
    assert history.data.loc[0, "date_removed_approximate"]
    assert pd.isna(history.data.loc[1, "date_removed_parsed"])
    assert history.data.loc[0, "cik"] == "0002"
    assert history.issues == audit_result.report.issues
    assert any(issue.issue_type == "approximate_date" for issue in history.issues)
    assert history.source == "synthetic-example"
    assert history.dataset_sha256 == "a" * 64
    assert history.coverage_start == date(2026, 1, 1)
    assert history.coverage_end == date(2026, 1, 31)


def test_prepared_data_and_issues_are_independent(
    membership_data: pd.DataFrame,
) -> None:
    audit_result = audit_membership(membership_data)
    audit_result_data_copy_1 = audit_result.data.copy(deep=True)
    audit_report_copy_1 = deepcopy(audit_result.report)
    history = prepare_history(
        audit_result,
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    assert_frame_equal(audit_result.data, audit_result_data_copy_1)
    assert audit_result.report == audit_report_copy_1

    history.data.loc[0, "name"] = "Changed for this test"
    history.issues[0].issue_details = "Changed for this test"
    history.issues.append(ValidationIssue("review_note", "Additional finding."))

    assert_frame_equal(audit_result.data, audit_result_data_copy_1)
    assert audit_result.report == audit_report_copy_1


def test_source_positions_ignore_index_labels_and_survive_sorting(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.index = pd.Index([20, 20], name="source_index")
    audit_result = audit_membership(membership_data)
    history = prepare_history(
        audit_result,
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    assert_frame_equal(history.data[audit_result.data.columns], audit_result.data)
    assert history.data["source_record_position"].tolist() == [0, 1]
    sorted_data = history.data.sort_values("symbol")
    assert sorted_data["symbol"].tolist() == ["ALFA", "BETA"]
    assert sorted_data["source_record_position"].tolist() == [1, 0]


def test_prepare_rejects_invalid_audit(membership_data: pd.DataFrame) -> None:
    membership_data.loc[0, "date_removed"] = "banana"
    audit_result = audit_membership(membership_data)
    # An invalid removal must not be mistaken for an open-ended interval.
    assert pd.isna(audit_result.data.loc[0, "date_removed_parsed"])
    with pytest.raises(ValueError, match="invalid audit"):
        prepare_history(
            audit_result,
            source="synthetic-example",
            dataset_sha256="a" * 64,
            coverage_start=date(2026, 1, 1),
            coverage_end=date(2026, 1, 31),
        )


def test_prepare_rejects_reversed_coverage(membership_data: pd.DataFrame) -> None:
    audit_result = audit_membership(membership_data)
    with pytest.raises(ValueError, match="Coverage start must not be later"):
        prepare_history(
            audit_result,
            source="synthetic-example",
            dataset_sha256="a" * 64,
            coverage_start=date(2026, 1, 31),
            coverage_end=date(2026, 1, 1),
        )


def test_one_day_coverage_preserves_all_history(
    membership_data: pd.DataFrame,
) -> None:
    audit_result = audit_membership(membership_data)
    history = prepare_history(
        audit_result,
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 1),
    )

    assert history.coverage_start == history.coverage_end == date(2026, 1, 1)
    # Preparation is not a membership query: keep earlier and later additions.
    assert_frame_equal(history.data[audit_result.data.columns], audit_result.data)


def test_prepare_retains_nonblocking_issues_without_normalizing(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.loc[0, "symbol"] = " beta "
    membership_data.loc[0, "name"] = " Beta Company "
    audit_result = audit_membership(membership_data)
    history = prepare_history(
        audit_result,
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    assert_frame_equal(history.data[audit_result.data.columns], audit_result.data)
    assert {"symbol_case", "surrounding_whitespace", "approximate_date"} <= {
        issue.issue_type for issue in history.issues
    }
    assert history.issues == audit_result.report.issues


@pytest.fixture
def prepared_history(membership_data: pd.DataFrame) -> PreparedHistory:
    return prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )


@pytest.mark.parametrize(
    "as_of,expected_symbols",
    [
        (date(2026, 1, 1), ["ALFA"]),
        (date(2026, 1, 7), ["ALFA"]),
        (date(2026, 1, 8), ["BETA", "ALFA"]),
        (date(2026, 1, 11), ["BETA", "ALFA"]),
        (date(2026, 1, 12), ["ALFA"]),
        (date(2026, 1, 31), ["ALFA"]),
    ],
)
def test_membership_date_boundaries(
    prepared_history: PreparedHistory,
    as_of: date,
    expected_symbols: list[str],
) -> None:
    snapshot = membership_at(prepared_history, as_of=as_of)

    assert snapshot.data["symbol"].tolist() == expected_symbols
    assert snapshot.as_of == as_of


@pytest.mark.parametrize(
    "as_of,expected_symbols",
    [
        (date(2026, 1, 9), ["ALFA"]),
        (date(2026, 1, 10), ["BETA", "ALFA"]),
    ],
)
def test_membership_respects_created_date(
    membership_data: pd.DataFrame,
    as_of: date,
    expected_symbols: list[str],
) -> None:
    membership_data.loc[0, "created_at"] = "2026-01-10"
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    snapshot = membership_at(history, as_of=as_of)

    assert snapshot.data["symbol"].tolist() == expected_symbols
    assert snapshot.as_of == as_of
    assert history.data.loc[0, "date_added"] == "2026-01-08*"


@pytest.mark.parametrize("as_of", [date(2025, 12, 31), date(2026, 2, 1)])
def test_membership_rejects_dates_outside_coverage(
    prepared_history: PreparedHistory, as_of: date
) -> None:
    with pytest.raises(ValueError, match="outside the supported coverage"):
        membership_at(prepared_history, as_of=as_of)


def test_empty_snapshot_preserves_schema_and_context(
    membership_data: pd.DataFrame,
) -> None:
    history = prepare_history(
        audit_membership(membership_data.iloc[[0]].copy()),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    snapshot = membership_at(history, as_of=date(2026, 1, 7))

    assert snapshot.data.empty
    assert_frame_equal(snapshot.data, history.data.iloc[:0])
    assert snapshot.as_of == date(2026, 1, 7)
    assert snapshot.source == history.source
    assert snapshot.dataset_sha256 == history.dataset_sha256
    assert snapshot.coverage_start == history.coverage_start
    assert snapshot.coverage_end == history.coverage_end
    # Issues describe the whole source, even when no rows qualify.
    assert snapshot.issues == history.issues


def test_snapshot_preserves_dates_flags_and_source_context(
    prepared_history: PreparedHistory,
) -> None:
    snapshot = membership_at(prepared_history, as_of=date(2026, 1, 10))

    assert_frame_equal(snapshot.data, prepared_history.data)
    assert snapshot.data.loc[0, "date_added"] == "2026-01-08*"
    assert snapshot.data.loc[0, "date_removed"] == "2026-01-12*"
    assert snapshot.data.loc[0, "date_added_approximate"]
    assert snapshot.data.loc[0, "date_removed_approximate"]
    assert snapshot.data["source_record_position"].tolist() == [0, 1]
    assert snapshot.source == prepared_history.source
    assert snapshot.dataset_sha256 == prepared_history.dataset_sha256
    assert snapshot.coverage_start == prepared_history.coverage_start
    assert snapshot.coverage_end == prepared_history.coverage_end
    assert snapshot.issues == prepared_history.issues


def test_snapshot_data_and_issues_are_independent(
    prepared_history: PreparedHistory,
) -> None:
    prepared_history_data_copy_1 = prepared_history.data.copy(deep=True)
    prepared_history_issues_copy_1 = deepcopy(prepared_history.issues)

    snapshot = membership_at(prepared_history, as_of=date(2026, 1, 10))

    assert_frame_equal(prepared_history.data, prepared_history_data_copy_1)
    assert prepared_history.issues == prepared_history_issues_copy_1

    snapshot.data.loc[0, "name"] = "Changed for this test"
    snapshot.issues[0].issue_details = "Changed for this test"
    snapshot.issues.append(ValidationIssue("review_note", "Snapshot finding."))

    assert_frame_equal(prepared_history.data, prepared_history_data_copy_1)
    assert prepared_history.issues == prepared_history_issues_copy_1


def test_snapshot_preserves_positions_with_duplicate_index_labels(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.index = pd.Index([20, 20], name="source_index")
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    snapshot = membership_at(history, as_of=date(2026, 1, 12))

    assert_frame_equal(snapshot.data, history.data.iloc[[1]])
    assert snapshot.data["symbol"].tolist() == ["ALFA"]
    assert snapshot.data["source_record_position"].tolist() == [1]
    assert snapshot.data.index.tolist() == [20]


def test_membership_keys_use_both_identifiers_and_preserve_rows(
    membership_data: pd.DataFrame,
) -> None:
    alfa_record = membership_data.iloc[[1]]
    records = pd.concat(
        [
            alfa_record,
            alfa_record.assign(cik="0003"),
            alfa_record.assign(symbol="DELTA"),
            alfa_record.assign(date_added="2025-12-16"),
        ],
        ignore_index=True,
    )
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    snapshot = membership_at(history, as_of=date(2026, 1, 10))
    snapshot_data_copy_1 = snapshot.data.copy(deep=True)

    assert membership_keys(snapshot) == {
        ("ALFA", "0001"),
        ("ALFA", "0003"),
        ("DELTA", "0001"),
    }
    assert_frame_equal(snapshot.data, snapshot_data_copy_1)
    assert len(snapshot.data) == 4


def test_empty_snapshot_has_no_membership_keys(
    membership_data: pd.DataFrame,
) -> None:
    history = prepare_history(
        audit_membership(membership_data.iloc[[0]].copy()),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    snapshot = membership_at(history, as_of=date(2026, 1, 7))

    assert membership_keys(snapshot) == set()


def test_compare_snapshots_reports_changes_and_preserves_evidence(
    membership_data: pd.DataFrame,
) -> None:
    delta_record = membership_data.iloc[[1]].assign(
        symbol="DELTA",
        cik="0003",
        name="Delta Company",
        date_added="2026-01-12",
        created_at="2026-01-12",
    )
    records = pd.concat([membership_data, delta_record], ignore_index=True)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    earlier = membership_at(history, as_of=date(2026, 1, 10))
    later = membership_at(history, as_of=date(2026, 1, 12))

    difference = compare_snapshots(earlier, later)

    assert difference.appeared == {("DELTA", "0003")}
    assert difference.disappeared == {("BETA", "0002")}
    assert difference.unchanged == {("ALFA", "0001")}
    assert_frame_equal(difference.earlier.data, earlier.data)
    assert_frame_equal(difference.later.data, later.data)
    assert difference.earlier.as_of == date(2026, 1, 10)
    assert difference.later.as_of == date(2026, 1, 12)
    assert difference.earlier.data["source_record_position"].tolist() == [0, 1]
    assert difference.later.data["source_record_position"].tolist() == [1, 2]
    assert difference.earlier.issues == earlier.issues
    assert difference.later.issues == later.issues
    assert difference.earlier.source == history.source
    assert difference.later.dataset_sha256 == history.dataset_sha256


def test_comparison_evidence_is_independent(prepared_history: PreparedHistory) -> None:
    earlier = membership_at(prepared_history, as_of=date(2026, 1, 10))
    later = membership_at(prepared_history, as_of=date(2026, 1, 12))
    earlier_snapshot_copy_1 = deepcopy(earlier)
    later_snapshot_copy_1 = deepcopy(later)

    difference = compare_snapshots(earlier, later)
    difference.earlier.data.loc[0, "name"] = "Changed earlier evidence"
    difference.later.data.loc[1, "name"] = "Changed later evidence"
    difference.earlier.issues[0].issue_details = "Changed earlier issue"
    difference.later.issues[0].issue_details = "Changed later issue"

    assert_frame_equal(earlier.data, earlier_snapshot_copy_1.data)
    assert_frame_equal(later.data, later_snapshot_copy_1.data)
    assert earlier.issues == earlier_snapshot_copy_1.issues
    assert later.issues == later_snapshot_copy_1.issues


def test_same_date_comparison_has_no_changes(prepared_history: PreparedHistory) -> None:
    snapshot = membership_at(prepared_history, as_of=date(2026, 1, 10))

    difference = compare_snapshots(snapshot, snapshot)

    assert difference.appeared == set()
    assert difference.disappeared == set()
    assert difference.unchanged == {("ALFA", "0001"), ("BETA", "0002")}


def test_compare_snapshots_rejects_reversed_dates(
    prepared_history: PreparedHistory,
) -> None:
    earlier = membership_at(prepared_history, as_of=date(2026, 1, 12))
    later = membership_at(prepared_history, as_of=date(2026, 1, 10))

    with pytest.raises(ValueError, match="Earlier snapshot date must not be after"):
        compare_snapshots(earlier, later)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "another-source"),
        ("dataset_sha256", "b" * 64),
        ("coverage_start", date(2026, 1, 2)),
        ("coverage_end", date(2026, 1, 30)),
    ],
)
def test_compare_snapshots_rejects_mismatched_context(
    prepared_history: PreparedHistory, field: str, value: str | date
) -> None:
    earlier = membership_at(prepared_history, as_of=date(2026, 1, 10))
    later = membership_at(prepared_history, as_of=date(2026, 1, 12))
    setattr(later, field, value)

    with pytest.raises(ValueError, match="same source, dataset version, and coverage"):
        compare_snapshots(earlier, later)


@pytest.mark.parametrize(
    "earlier_date,later_date,expected_appeared,expected_disappeared",
    [
        (date(2026, 1, 7), date(2026, 1, 10), {("BETA", "0002")}, set()),
        (date(2026, 1, 10), date(2026, 1, 13), set(), {("BETA", "0002")}),
        (date(2026, 1, 2), date(2026, 1, 7), set(), set()),
    ],
)
def test_comparison_handles_empty_snapshots(
    membership_data: pd.DataFrame,
    earlier_date: date,
    later_date: date,
    expected_appeared: set[tuple[str, str]],
    expected_disappeared: set[tuple[str, str]],
) -> None:
    history = prepare_history(
        audit_membership(membership_data.iloc[[0]].copy()),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    earlier = membership_at(history, as_of=earlier_date)
    later = membership_at(history, as_of=later_date)

    difference = compare_snapshots(earlier, later)

    assert difference.appeared == expected_appeared
    assert difference.disappeared == expected_disappeared
    assert difference.unchanged == set()


def test_reentry_between_snapshots_is_unchanged_at_endpoints(
    membership_data: pd.DataFrame,
) -> None:
    reentry_record = membership_data.iloc[[0]].assign(
        date_added="2026-01-20",
        date_removed=None,
        created_at="2026-01-20",
    )
    records = pd.concat([membership_data, reentry_record], ignore_index=True)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    earlier = membership_at(history, as_of=date(2026, 1, 10))
    later = membership_at(history, as_of=date(2026, 1, 21))

    difference = compare_snapshots(earlier, later)

    assert difference.appeared == set()
    assert difference.disappeared == set()
    assert difference.unchanged == {("BETA", "0002"), ("ALFA", "0001")}
    assert difference.earlier.data["source_record_position"].tolist() == [0, 1]
    assert difference.later.data["source_record_position"].tolist() == [1, 2]


def test_addition_events_keep_nominal_dates_and_addition_flags(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.loc[0, "date_removed"] = "2026-01-12"
    membership_data.loc[0, "created_at"] = "2026-01-10"
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    history_data_copy_1 = history.data.copy(deep=True)

    additions = _addition_events(history)

    assert additions["symbol"].tolist() == ["BETA"]
    assert additions["cik"].tolist() == ["0002"]
    assert additions["event_date"].tolist() == [pd.Timestamp("2026-01-08")]
    assert additions["created_at_parsed"].tolist() == [pd.Timestamp("2026-01-10")]
    assert additions["event_date_approximate"].tolist() == [True]
    assert additions["event_type"].tolist() == ["ADD"]
    assert additions["source_record_position"].tolist() == [0]
    assert not history.data.loc[0, "date_removed_approximate"]
    assert_frame_equal(history.data, history_data_copy_1)

    additions.loc[0, "symbol"] = "CHANGED"
    assert_frame_equal(history.data, history_data_copy_1)


@pytest.mark.parametrize(
    "added_date,expected_count",
    [
        ("2025-12-31", 0),
        ("2026-01-01", 0),
        ("2026-01-02", 1),
        ("2026-01-31", 1),
        ("2026-02-01", 0),
    ],
)
def test_addition_events_apply_coverage_boundaries(
    membership_data: pd.DataFrame, added_date: str, expected_count: int
) -> None:
    records = membership_data.iloc[[1]].assign(date_added=added_date)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    additions = _addition_events(history)

    assert len(additions) == expected_count
    assert additions.columns.tolist() == [
        "symbol",
        "cik",
        "event_date",
        "event_date_approximate",
        "created_at_parsed",
        "source_record_position",
        "event_type",
    ]
    assert additions["event_date"].dtype == history.data["date_added_parsed"].dtype
    assert (
        additions["event_date_approximate"].dtype
        == history.data["date_added_approximate"].dtype
    )
    if expected_count:
        assert additions["event_date"].tolist() == [pd.Timestamp(added_date)]
        assert additions["event_date_approximate"].tolist() == [False]


def test_addition_events_preserve_reentry_and_distinct_source_records(
    membership_data: pd.DataFrame,
) -> None:
    beta_record = membership_data.iloc[[0]]
    reentry_record = beta_record.assign(
        date_added="2026-01-20", date_removed=None, created_at="2026-01-20"
    )
    records = pd.concat(
        [beta_record, reentry_record, beta_record.assign(cik="0003")],
        ignore_index=True,
    )
    records.index = pd.Index([20, 20, 20], name="source_index")
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    additions = _addition_events(history)

    assert additions["symbol"].tolist() == ["BETA", "BETA", "BETA"]
    assert additions["cik"].tolist() == ["0002", "0002", "0003"]
    assert additions["event_date"].tolist() == [
        pd.Timestamp("2026-01-08"),
        pd.Timestamp("2026-01-20"),
        pd.Timestamp("2026-01-08"),
    ]
    assert additions["event_date_approximate"].tolist() == [True, False, True]
    assert additions["source_record_position"].tolist() == [0, 1, 2]
    assert additions.index.tolist() == [20, 20, 20]
    assert_frame_equal(additions, _addition_events(history))


def test_one_day_coverage_has_no_addition_events(
    prepared_history: PreparedHistory,
) -> None:
    prepared_history.coverage_start = date(2026, 1, 8)
    prepared_history.coverage_end = date(2026, 1, 8)

    assert _addition_events(prepared_history).empty


def test_removal_events_keep_nominal_dates_and_removal_flags(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.loc[0, "date_added"] = "2026-01-08"
    membership_data.loc[0, "created_at"] = "2026-01-15"
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    history_data_copy_1 = history.data.copy(deep=True)

    removals = _removal_events(history)

    assert removals["symbol"].tolist() == ["BETA"]
    assert removals["cik"].tolist() == ["0002"]
    assert removals["event_date"].tolist() == [pd.Timestamp("2026-01-12")]
    assert removals["created_at_parsed"].tolist() == [pd.Timestamp("2026-01-15")]
    assert removals["event_date_approximate"].tolist() == [True]
    assert removals["event_type"].tolist() == ["REMOVE"]
    assert removals["source_record_position"].tolist() == [0]
    assert not history.data.loc[0, "date_added_approximate"]
    assert_frame_equal(history.data, history_data_copy_1)

    removals.loc[0, "symbol"] = "CHANGED"
    assert_frame_equal(history.data, history_data_copy_1)


@pytest.mark.parametrize(
    "removed_date,expected_count",
    [
        (None, 0),
        ("2025-12-31", 0),
        ("2026-01-01", 0),
        ("2026-01-02", 1),
        ("2026-01-31", 1),
        ("2026-02-01", 0),
    ],
)
def test_removal_events_apply_coverage_and_skip_missing_dates(
    membership_data: pd.DataFrame, removed_date: str | None, expected_count: int
) -> None:
    records = membership_data.iloc[[1]].assign(date_removed=removed_date)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    removals = _removal_events(history)

    assert len(removals) == expected_count
    assert removals.columns.tolist() == [
        "symbol",
        "cik",
        "event_date",
        "event_date_approximate",
        "created_at_parsed",
        "source_record_position",
        "event_type",
    ]
    assert removals["event_date"].dtype == history.data["date_removed_parsed"].dtype
    assert (
        removals["event_date_approximate"].dtype
        == history.data["date_removed_approximate"].dtype
    )
    if expected_count:
        assert removed_date is not None
        assert removals["event_date"].tolist() == [pd.Timestamp(removed_date)]
        assert removals["event_date_approximate"].tolist() == [False]


def test_removal_events_preserve_repeated_intervals_and_source_records(
    membership_data: pd.DataFrame,
) -> None:
    beta_record = membership_data.iloc[[0]]
    reentry_record = beta_record.assign(
        date_added="2026-01-20",
        date_removed="2026-01-25",
        created_at="2026-01-20",
    )
    records = pd.concat(
        [beta_record, reentry_record, beta_record.assign(cik="0003")],
        ignore_index=True,
    )
    records.index = pd.Index([20, 20, 20], name="source_index")
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    removals = _removal_events(history)

    assert removals["symbol"].tolist() == ["BETA", "BETA", "BETA"]
    assert removals["cik"].tolist() == ["0002", "0002", "0003"]
    assert removals["event_date"].tolist() == [
        pd.Timestamp("2026-01-12"),
        pd.Timestamp("2026-01-25"),
        pd.Timestamp("2026-01-12"),
    ]
    assert removals["event_date_approximate"].tolist() == [True, False, True]
    assert removals["source_record_position"].tolist() == [0, 1, 2]
    assert removals.index.tolist() == [20, 20, 20]
    assert_frame_equal(removals, _removal_events(history))


def test_one_day_coverage_has_no_removal_events(
    prepared_history: PreparedHistory,
) -> None:
    prepared_history.coverage_start = date(2026, 1, 12)
    prepared_history.coverage_end = date(2026, 1, 12)

    assert _removal_events(prepared_history).empty


def test_combined_events_preserve_chronology_reentry_and_source_positions(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.loc[1, "date_removed"] = "2026-01-15"
    reentry_record = membership_data.iloc[[0]].assign(
        date_added="2026-01-20", date_removed=None, created_at="2026-01-20"
    )
    records = pd.concat([membership_data, reentry_record], ignore_index=True)
    records.index = pd.Index([20, 20, 20], name="source_index")
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    history_data_copy_1 = history.data.copy(deep=True)

    events = _event_rows(history)

    assert events["symbol"].tolist() == ["BETA", "BETA", "ALFA", "BETA"]
    assert events["event_date"].tolist() == [
        pd.Timestamp("2026-01-08"),
        pd.Timestamp("2026-01-12"),
        pd.Timestamp("2026-01-15"),
        pd.Timestamp("2026-01-20"),
    ]
    assert events["event_type"].tolist() == ["ADD", "REMOVE", "REMOVE", "ADD"]
    assert events["event_date_approximate"].tolist() == [True, True, False, False]
    assert events["source_record_position"].tolist() == [0, 0, 1, 2]
    assert events.index.tolist() == [0, 1, 2, 3]
    assert_frame_equal(events, _event_rows(history))
    assert_frame_equal(history.data, history_data_copy_1)

    events.loc[0, "symbol"] = "CHANGED"
    assert_frame_equal(history.data, history_data_copy_1)


def test_same_date_events_keep_both_endpoints_and_sort_all_source_records(
    membership_data: pd.DataFrame,
) -> None:
    beta_record = membership_data.iloc[[0]].assign(
        date_added="2026-01-10",
        date_removed="2026-01-10",
        created_at="2026-01-10",
    )
    alfa_record = membership_data.iloc[[1]].assign(
        date_added="2026-01-10", created_at="2026-01-10"
    )
    records = pd.concat(
        [
            beta_record,
            alfa_record.assign(cik="0003"),
            alfa_record.assign(date_added="2026-01-10*"),
            alfa_record,
        ],
        ignore_index=True,
    )
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    events = _event_rows(history)

    assert events["event_date"].tolist() == [pd.Timestamp("2026-01-10")] * 5
    assert events["symbol"].tolist() == ["ALFA", "ALFA", "ALFA", "BETA", "BETA"]
    assert events["cik"].tolist() == ["0001", "0001", "0003", "0002", "0002"]
    assert events["event_type"].tolist() == ["ADD", "ADD", "ADD", "ADD", "REMOVE"]
    assert events["source_record_position"].tolist() == [2, 3, 1, 0, 0]
    assert events["event_date_approximate"].tolist() == [
        True,
        False,
        False,
        False,
        False,
    ]
    assert any(issue.issue_type == "equal_endpoints" for issue in history.issues)

    history.data = history.data.iloc[::-1].copy()
    assert_frame_equal(events, _event_rows(history))


@pytest.mark.parametrize(
    "added_date,removed_date,expected_types",
    [
        ("2026-01-05", None, ["ADD"]),
        ("2025-12-15", "2026-01-15", ["REMOVE"]),
        ("2025-12-15", None, []),
    ],
)
def test_combined_events_handle_empty_branches_with_consistent_schema(
    membership_data: pd.DataFrame,
    prepared_history: PreparedHistory,
    added_date: str,
    removed_date: str | None,
    expected_types: list[str],
) -> None:
    records = membership_data.iloc[[1]].assign(
        date_added=added_date, date_removed=removed_date
    )
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    events = _event_rows(history)

    assert events["event_type"].tolist() == expected_types
    assert events["source_record_position"].tolist() == [0] * len(expected_types)
    assert events.index.tolist() == list(range(len(expected_types)))
    assert_frame_equal(events.iloc[:0], _event_rows(prepared_history).iloc[:0])


@pytest.mark.parametrize(
    "created_date,expected_flags",
    [
        ("2026-01-07", [False, False]),
        ("2026-01-08", [False, False]),
        ("2026-01-10", [True, False]),
        ("2026-01-12", [True, False]),
        ("2026-01-15", [True, True]),
    ],
)
def test_created_after_event_flags_each_nominal_date_without_changing_it(
    membership_data: pd.DataFrame,
    created_date: str,
    expected_flags: list[bool],
) -> None:
    membership_data.loc[0, "created_at"] = created_date
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    history_data_copy_1 = history.data.copy(deep=True)

    events = _event_rows(history)

    assert events["created_after_event"].tolist() == expected_flags
    assert events["created_after_event"].dtype == bool
    assert events["event_date"].tolist() == [
        pd.Timestamp("2026-01-08"),
        pd.Timestamp("2026-01-12"),
    ]
    assert events["event_type"].tolist() == ["ADD", "REMOVE"]
    assert events["event_date_approximate"].tolist() == [True, True]
    assert events["source_record_position"].tolist() == [0, 0]
    assert_frame_equal(history.data, history_data_copy_1)


def test_source_event_table_retains_context_and_complete_evidence(
    prepared_history: PreparedHistory,
) -> None:
    prepared_history.data.index = pd.Index([20, 20], name="source_index")
    prepared_history.data["source_note"] = "Retain this extra source column"
    prepared_history.data = prepared_history.data.iloc[::-1].copy()

    event_table = build_source_events(prepared_history)

    assert event_table.source == prepared_history.source
    assert event_table.dataset_sha256 == prepared_history.dataset_sha256
    assert event_table.coverage_start == prepared_history.coverage_start
    assert event_table.coverage_end == prepared_history.coverage_end
    assert event_table.issues == prepared_history.issues
    assert_frame_equal(event_table.source_data, prepared_history.data)
    assert event_table.data["event_type"].tolist() == ["ADD", "REMOVE"]
    assert event_table.data["source_record_position"].tolist() == [0, 0]
    assert event_table.data["event_date"].tolist() == [
        pd.Timestamp("2026-01-08"),
        pd.Timestamp("2026-01-12"),
    ]
    source_record_position = event_table.data.loc[0, "source_record_position"]
    source_record = event_table.source_data.loc[
        event_table.source_data["source_record_position"] == source_record_position
    ]
    assert len(source_record) == 1
    assert source_record["symbol"].tolist() == ["BETA"]
    assert source_record["cik"].tolist() == ["0002"]
    assert source_record["date_added"].tolist() == ["2026-01-08*"]
    assert source_record["date_removed"].tolist() == ["2026-01-12*"]
    assert source_record["source_note"].tolist() == ["Retain this extra source column"]


def test_source_event_table_data_evidence_and_issues_are_independent(
    prepared_history: PreparedHistory,
) -> None:
    history_data_copy_1 = prepared_history.data.copy(deep=True)
    history_issues_copy_1 = deepcopy(prepared_history.issues)

    event_table = build_source_events(prepared_history)
    event_table.data.loc[0, "symbol"] = "CHANGED"
    assert_frame_equal(event_table.source_data, history_data_copy_1)

    event_table.source_data.loc[0, "name"] = "Changed evidence"
    event_table.issues[0].issue_details = "Changed issue"
    event_table.issues.append(ValidationIssue("review_note", "Result-only finding."))

    assert_frame_equal(prepared_history.data, history_data_copy_1)
    assert prepared_history.issues == history_issues_copy_1


def test_empty_source_event_table_retains_schema_context_and_source_issues(
    prepared_history: PreparedHistory,
) -> None:
    populated_table = build_source_events(prepared_history)
    prepared_history.coverage_start = date(2026, 1, 8)
    prepared_history.coverage_end = date(2026, 1, 8)

    event_table = build_source_events(prepared_history)

    assert event_table.data.empty
    assert_frame_equal(event_table.data, populated_table.data.iloc[:0])
    assert_frame_equal(event_table.source_data, prepared_history.data)
    assert event_table.source == prepared_history.source
    assert event_table.dataset_sha256 == prepared_history.dataset_sha256
    assert event_table.coverage_start == date(2026, 1, 8)
    assert event_table.coverage_end == date(2026, 1, 8)
    assert event_table.issues == prepared_history.issues
    assert any(issue.issue_type == "approximate_date" for issue in event_table.issues)


def test_equal_endpoint_events_retain_the_audit_issue_and_raw_dates(
    membership_data: pd.DataFrame,
) -> None:
    membership_data.loc[0, "date_removed"] = "2026-01-08"
    history = prepare_history(
        audit_membership(membership_data),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    event_table = build_source_events(history)

    assert event_table.data["event_type"].tolist() == ["ADD", "REMOVE"]
    assert event_table.data["event_date"].tolist() == [pd.Timestamp("2026-01-08")] * 2
    assert event_table.data["event_date_approximate"].tolist() == [True, False]
    assert event_table.data["source_record_position"].tolist() == [0, 0]
    assert any(issue.issue_type == "equal_endpoints" for issue in event_table.issues)
    assert event_table.source_data.loc[0, "date_added"] == "2026-01-08*"
    assert event_table.source_data.loc[0, "date_removed"] == "2026-01-08"
    snapshot = membership_at(history, as_of=date(2026, 1, 8))
    assert snapshot.data["symbol"].tolist() == ["ALFA"]


def test_snapshot_flags_repeated_active_identity_without_dropping_evidence(
    membership_data: pd.DataFrame,
) -> None:
    overlapping_record = membership_data.iloc[[1]].assign(date_added="2025-12-16")
    records = pd.concat([membership_data, overlapping_record], ignore_index=True)
    records.index = pd.Index([20, 20, 20])
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )
    history_issues_copy_1 = deepcopy(history.issues)

    snapshot = membership_at(history, date(2026, 1, 10))

    assert len(snapshot.data) == 3
    assert len(membership_keys(snapshot)) == 2
    assert snapshot.data["source_record_position"].tolist() == [0, 1, 2]
    issue = next(
        issue
        for issue in snapshot.issues
        if issue.issue_type == "duplicate_active_identity"
    )
    assert issue.row_count == 2
    assert "2026-01-10" in issue.issue_details
    assert not any(
        issue.issue_type == "multiple_active_ciks" for issue in snapshot.issues
    )
    assert history.issues == history_issues_copy_1
    comparison = compare_snapshots(snapshot, snapshot)
    assert comparison.earlier.issues == snapshot.issues
    assert comparison.later.issues == snapshot.issues


def test_snapshot_flags_same_symbol_with_multiple_active_ciks(
    membership_data: pd.DataFrame,
) -> None:
    another_identity = membership_data.iloc[[0]].assign(cik="0003")
    records = pd.concat([membership_data, another_identity], ignore_index=True)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    snapshot = membership_at(history, date(2026, 1, 10))

    assert len(snapshot.data) == 3
    assert membership_keys(snapshot) == {
        ("BETA", "0002"),
        ("BETA", "0003"),
        ("ALFA", "0001"),
    }
    assert snapshot.data["symbol"].nunique() == 2
    issue = next(
        issue for issue in snapshot.issues if issue.issue_type == "multiple_active_ciks"
    )
    assert issue.row_count == 2
    assert "2026-01-10" in issue.issue_details
    assert not any(
        issue.issue_type == "duplicate_active_identity" for issue in snapshot.issues
    )


@pytest.mark.parametrize("as_of", [date(2026, 1, 11), date(2026, 1, 12)])
def test_nonoverlapping_identities_do_not_raise_snapshot_ambiguity_warnings(
    membership_data: pd.DataFrame, as_of: date
) -> None:
    later_identity = membership_data.iloc[[0]].assign(
        cik="0003",
        date_added="2026-01-12",
        date_removed=None,
        created_at="2026-01-12",
    )
    records = pd.concat([membership_data, later_identity], ignore_index=True)
    history = prepare_history(
        audit_membership(records),
        source="synthetic-example",
        dataset_sha256="a" * 64,
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 31),
    )

    snapshot = membership_at(history, as_of)

    assert len(snapshot.data) == 2
    assert not any(
        issue.issue_type in {"duplicate_active_identity", "multiple_active_ciks"}
        for issue in snapshot.issues
    )
