"""Tests for membership CSV validation, loading, and command-line reporting."""

import json
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from indexlens.data_audit import (
    EXPECTED_COLUMNS,
    audit_membership,
    load_membership_csv,
    main,
)


def membership(**changes: str | None) -> pd.DataFrame:
    row = {
        "symbol": "EXAMPLE",
        "cik": "0000123",
        "name": "Example Company",
        "sector": "industrials",
        "date_added": "2026-01-01",
        "date_removed": None,
        "created_at": "2026-01-01",
    }
    row.update(changes)
    return pd.DataFrame([row], dtype="string")


def issue_types(frame: pd.DataFrame) -> set[str]:
    return {issue.issue_type for issue in audit_membership(frame).report.issues}


def test_preserve_input() -> None:
    frame = membership(date_added="2026-01-01*")
    frame.index = pd.Index([42], name="source_row")
    original = frame.copy(deep=True)
    result = audit_membership(frame)
    assert result.report.is_valid
    assert_frame_equal(frame, original)
    assert_frame_equal(result.data[list(EXPECTED_COLUMNS)], original)
    assert result.data.loc[42, "date_added_parsed"] == pd.Timestamp("2026-01-01")
    assert result.data.loc[42, "date_added_approximate"]
    assert result.report.approximate_counts["date_added"] == 1
    assert result.report.missing_counts["date_removed"] == 1
    assert result.report.date_ranges["date_removed"] == {
        "earliest": None,
        "latest": None,
    }


@pytest.mark.parametrize("column", [c for c in EXPECTED_COLUMNS if c != "date_removed"])
@pytest.mark.parametrize(
    "value,issue_type", [(None, "missing_required"), ("  ", "blank_required")]
)
def test_required_values(column: str, value: str | None, issue_type: str) -> None:
    result = audit_membership(membership(**{column: value}))
    assert not result.report.is_valid
    assert any(
        i.issue_type == issue_type and i.column == column and i.row_count == 1
        for i in result.report.issues
    )


@pytest.mark.parametrize(
    "value",
    [
        "banana",
        "2026-02-30",
        "2026-1-01",
        "01/01/2026",
        "2026-01-01**",
        "2026-01-01 ",
        "",
    ],
)
def test_invalid_removal_dates(value: str) -> None:
    result = audit_membership(membership(date_removed=value))
    assert not result.report.is_valid
    assert any(
        i.issue_type == "malformed_date" and i.column == "date_removed"
        for i in result.report.issues
    )
    assert result.report.missing_counts["date_removed"] == 0


def test_missing_added_date() -> None:
    result = audit_membership(membership(date_added=None))
    assert not result.report.is_valid
    assert not any(i.issue_type == "malformed_date" for i in result.report.issues)


def test_date_approximation() -> None:
    # Membership dates allow *, while created_at requires an exact date.
    result = audit_membership(membership(date_removed="2026-02-01*"))
    assert result.report.is_valid
    assert result.report.approximate_counts["date_removed"] == 1
    assert "malformed_date" in issue_types(membership(created_at="2026-01-01*"))


def test_interval_boundaries() -> None:
    # Reversed intervals fail; equal endpoints remain valid but need review.
    reversed_result = audit_membership(membership(date_removed="2025-12-31*"))
    assert not reversed_result.report.is_valid
    assert "reversed_interval" in {i.issue_type for i in reversed_result.report.issues}
    equal_result = audit_membership(membership(date_removed="2026-01-01"))
    assert equal_result.report.is_valid
    assert "equal_endpoints" in {i.issue_type for i in equal_result.report.issues}


def test_duplicate_row_counts() -> None:
    # Three identical rows mean two repeats and three affected rows.
    frame = pd.concat([membership()] * 3, ignore_index=True)
    result = audit_membership(frame)
    assert not result.report.is_valid
    assert result.report.exact_duplicate_rows == 2
    issue = next(i for i in result.report.issues if i.issue_type == "duplicate_row")
    assert issue.row_count == 3


def test_repeated_identifiers() -> None:
    # Repeated identifiers and six-character symbols are valid here.
    frame = pd.concat(
        [
            membership(symbol="SIXCHR"),
            membership(symbol="SIXCHR", date_added="2026-02-01"),
        ],
        ignore_index=True,
    )
    result = audit_membership(frame)
    assert result.report.is_valid
    assert not result.report.issues
    assert result.report.repeated_counts["symbol"] == 1
    assert result.report.repeated_counts["cik"] == 1


def test_normalized_key_collision() -> None:
    frame = pd.concat([membership(), membership(date_added="2026-01-01*")])
    result = audit_membership(frame)
    assert result.report.is_valid
    assert len(result.data) == 2
    assert "duplicate_normalized_key" in {i.issue_type for i in result.report.issues}
    assert "duplicate_raw_key" not in {i.issue_type for i in result.report.issues}


def test_raw_key_collision() -> None:
    frame = pd.concat([membership(), membership(name="Different Name")])
    result = audit_membership(frame)
    assert result.report.is_valid
    assert "duplicate_raw_key" in {i.issue_type for i in result.report.issues}
    assert result.report.exact_duplicate_rows == 0


def test_missing_column() -> None:
    result = audit_membership(membership().drop(columns=["date_added"]))
    assert not result.report.is_valid
    assert any(
        i.issue_type == "missing_column"
        and i.column == "date_added"
        and i.row_count is None
        for i in result.report.issues
    )
    assert "date_removed_parsed" in result.data


def test_preserve_text_and_extra_columns() -> None:
    frame = membership(symbol=" abc ").assign(note="source text")
    result = audit_membership(frame)
    assert result.report.is_valid
    assert {"unexpected_column", "surrounding_whitespace", "symbol_case"} <= {
        i.issue_type for i in result.report.issues
    }
    assert_frame_equal(result.data[list(frame.columns)], frame)


def test_duplicate_index_counts() -> None:
    frame = pd.concat([membership(), membership(date_removed="bad")])
    assert frame.index.tolist() == [0, 0]
    result = audit_membership(frame)
    issue = next(i for i in result.report.issues if i.issue_type == "malformed_date")
    assert issue.row_count == 1
    assert result.data.index.tolist() == [0, 0]
    assert pd.isna(result.data["date_removed_parsed"].iloc[1])


def test_empty_table() -> None:
    result = audit_membership(membership().iloc[:0])
    assert not result.report.is_valid
    issue = next(i for i in result.report.issues if i.issue_type == "empty_dataset")
    assert issue.row_count is None


def test_ambiguous_columns() -> None:
    with pytest.raises(ValueError, match="unique strings"):
        audit_membership(pd.DataFrame([[1, 2]], columns=["symbol", "symbol"]))
    with pytest.raises(ValueError, match="generated"):
        audit_membership(membership().assign(date_added_parsed="existing"))


def test_loader_preserves_data(tmp_path: Path) -> None:
    # Preserve identifier zeros and leave the source file unchanged.
    path = tmp_path / "source.csv"
    membership().to_csv(path, index=False)
    original = path.read_bytes()
    result = audit_membership(load_membership_csv(path))
    assert result.data.loc[0, "cik"] == "0000123"
    assert result.report.is_valid
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "content",
    [
        "",
        "symbol,symbol\nA,B\n",
        "symbol,\nA,B\n",
        "symbol,cik\nA,1,extra\n",
        "symbol,cik\nA\n",
    ],
)
def test_loader_invalid_csv(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_membership_csv(path)


def test_loader_bom_and_commas(tmp_path: Path) -> None:
    path = tmp_path / "quoted.csv"
    membership(name="Example, Inc.").to_csv(path, index=False, encoding="utf-8-sig")
    assert load_membership_csv(path).loc[0, "name"] == "Example, Inc."


@pytest.mark.parametrize(
    "removal,expected", [(None, 0), ("2026-02-01*", 0), ("bad", 1)]
)
def test_cli_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    removal: str | None,
    expected: int,
) -> None:
    path = tmp_path / "sample.csv"
    membership(date_removed=removal).to_csv(path, index=False)
    assert main([str(path)]) == expected
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["is_valid"] == (expected == 0)
    assert report["rows"] == 1
    for issue in report["issues"]:
        assert set(issue) == {"issue_type", "issue_details", "column", "row_count"}
        assert issue["row_count"] == 1
    assert not output.err


def test_cli_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "missing.csv")]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "Cannot audit CSV" in output.err
