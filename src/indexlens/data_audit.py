"""Historical Index Membership Data Audit

Run with: python -m indexlens.data_audit path/to/components_history.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = (
    "symbol",
    "cik",
    "name",
    "sector",
    "date_added",
    "date_removed",
    "created_at",
)
REQUIRED_COLUMNS = tuple(c for c in EXPECTED_COLUMNS if c != "date_removed")
DATE_COLUMNS = ("date_added", "date_removed", "created_at")
MEMBERSHIP_DATES = ("date_added", "date_removed")
INVALID_ISSUE_TYPES = {
    "empty_dataset",
    "missing_column",
    "missing_required",
    "blank_required",
    "duplicate_row",
    "malformed_date",
    "reversed_interval",
}


@dataclass
class ValidationIssue:
    """A finding with an affected-row count; None if it does not apply to rows."""

    issue_type: str
    issue_details: str
    column: str | None = None
    row_count: int | None = None


@dataclass
class AuditReport:
    rows: int
    columns: list[str]
    dtypes: dict[str, str]
    missing_counts: dict[str, int] = field(default_factory=dict)
    blank_counts: dict[str, int] = field(default_factory=dict)
    unique_counts: dict[str, int] = field(default_factory=dict)
    repeated_counts: dict[str, int] = field(default_factory=dict)
    approximate_counts: dict[str, int] = field(default_factory=dict)
    date_ranges: dict[str, dict[str, str | None]] = field(default_factory=dict)
    exact_duplicate_rows: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether the data passes the required validation checks."""
        return not any(issue.issue_type in INVALID_ISSUE_TYPES for issue in self.issues)

    def to_json(self) -> str:
        return json.dumps({"is_valid": self.is_valid, **asdict(self)}, indent=2)


@dataclass
class AuditResult:
    """A copy of the data with parsed dates, approximation flags, and a report."""

    data: pd.DataFrame
    report: AuditReport


def load_membership_csv(path: str | Path) -> pd.DataFrame:
    """Load a local membership CSV as text, preserving leading zeros.

    Treat empty fields and missing-value markers such as NA as missing values.
    Require unique, non-empty column names and consistent row widths.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader, None)
        if not header or any(not name.strip() for name in header):
            raise ValueError("CSV must have a non-empty header with named columns.")
        if len(header) != len(set(header)):
            raise ValueError("CSV contains duplicate column names.")
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"CSV record ending at line {reader.line_num} has {len(row)} "
                    f"fields; expected {len(header)}."
                )
        source.seek(0)
        return pd.read_csv(source, dtype="string")


def parse_date_column(
    values: pd.Series, *, allow_approximate: bool
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return parsed dates, approximation flags, and invalid-date flags.

    Accept YYYY-MM-DD, with one trailing * when allow_approximate is True.
    Missing inputs are not treated as invalid dates.
    Inputs with surrounding whitespace are invalid.
    """
    text = values.astype("string")
    approximate = text.str.endswith("*", na=False) & allow_approximate
    cleaned = text.str.removesuffix("*") if allow_approximate else text
    shaped = cleaned.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", na=False)
    parsed = pd.to_datetime(cleaned.where(shaped), format="%Y-%m-%d", errors="coerce")
    malformed = text.notna() & parsed.isna()
    return parsed, approximate, malformed


def audit_membership(frame: pd.DataFrame) -> AuditResult:
    """Return a data copy with parsed dates and a validation report.

    Preserve original values and rows. Report missing columns and skip checks
    that require them. Raise ValueError for duplicate or non-string column
    names, or existing columns reserved for parsed dates and approximation flags.
    """
    if not frame.columns.is_unique or not all(
        isinstance(column, str) for column in frame.columns
    ):
        raise ValueError("DataFrame columns must be unique strings.")
    generated = {f"{c}_parsed" for c in DATE_COLUMNS} | {
        f"{c}_approximate" for c in MEMBERSHIP_DATES
    }
    if generated.intersection(frame.columns):
        raise ValueError("Input already contains generated parsed/approximate columns.")

    work = frame.reset_index(drop=True).astype("string")
    data = frame.copy(deep=True)
    report = AuditReport(
        rows=len(frame),
        columns=list(frame.columns),
        dtypes={str(column): str(dtype) for column, dtype in frame.dtypes.items()},
    )

    def add_issue(
        issue_type: str,
        issue_details: str,
        column: str | None = None,
        mask: pd.Series | None = None,
    ) -> None:
        if mask is None:
            row_count = None
        else:
            row_count = int(mask.fillna(False).sum())
            if row_count == 0:
                return
        report.issues.append(
            ValidationIssue(issue_type, issue_details, column, row_count)
        )

    if work.empty:
        add_issue("empty_dataset", "No data records to validate.")
    for column in EXPECTED_COLUMNS:
        if column not in work:
            add_issue("missing_column", "Expected column is absent.", column)
    for column in work.columns:
        if column not in EXPECTED_COLUMNS:
            add_issue("unexpected_column", "Extra column preserved.", column)
        values = work[column]
        missing = values.isna()
        blank = values.str.strip().eq("").fillna(False)
        report.missing_counts[column] = int(missing.sum())
        report.blank_counts[column] = int(blank.sum())
        if column in REQUIRED_COLUMNS:
            add_issue(
                "missing_required",
                "Required value is missing.",
                column,
                missing,
            )
            add_issue("blank_required", "Required value is blank.", column, blank)
        add_issue(
            "surrounding_whitespace",
            "Whitespace is preserved; review before normalization.",
            column,
            values.ne(values.str.strip()),
        )

    report.exact_duplicate_rows = int(work.duplicated().sum())
    add_issue(
        "duplicate_row",
        "Exact duplicate records (all copies counted).",
        mask=work.duplicated(keep=False),
    )
    for column in ("symbol", "cik", "sector"):
        if column in work:
            non_null = work[column].dropna()
            report.unique_counts[column] = int(non_null.nunique())
            report.repeated_counts[column] = int(non_null.duplicated().sum())
    if "symbol" in work:
        add_issue(
            "symbol_case",
            "Non-uppercase symbol preserved for review.",
            "symbol",
            work["symbol"].ne(work["symbol"].str.upper()),
        )

    parsed_dates: dict[str, pd.Series] = {}
    for column in DATE_COLUMNS:
        if column not in work:
            continue
        parsed, approximate, malformed = parse_date_column(
            work[column], allow_approximate=column in MEMBERSHIP_DATES
        )
        parsed_dates[column] = parsed
        data[f"{column}_parsed"] = parsed.array
        if column in MEMBERSHIP_DATES:
            data[f"{column}_approximate"] = approximate.array
            report.approximate_counts[column] = int(approximate.sum())
            add_issue(
                "approximate_date",
                "Source marks this date as approximate.",
                column,
                approximate,
            )
        add_issue(
            "malformed_date",
            "Non-null value has an invalid date format or calendar date.",
            column,
            malformed,
        )
        valid = parsed.dropna()
        report.date_ranges[column] = {
            "earliest": valid.min().strftime("%Y-%m-%d") if not valid.empty else None,
            "latest": valid.max().strftime("%Y-%m-%d") if not valid.empty else None,
        }

    if all(column in parsed_dates for column in MEMBERSHIP_DATES):
        added, removed = (parsed_dates[column] for column in MEMBERSHIP_DATES)
        comparable = added.notna() & removed.notna()
        add_issue(
            "reversed_interval",
            "Nominal removal precedes addition.",
            mask=comparable & removed.lt(added),
        )
        add_issue(
            "equal_endpoints",
            "Addition and removal dates are equal.",
            mask=comparable & removed.eq(added),
        )
    if "symbol" in work and "date_added" in parsed_dates:
        eligible = work["symbol"].notna() & work["symbol"].str.strip().ne("")
        raw_key = work[["symbol", "date_added"]]
        add_issue(
            "duplicate_raw_key",
            "Multiple rows have the same symbol and original date_added value.",
            mask=eligible & work["date_added"].notna() & raw_key.duplicated(keep=False),
        )
        normalized_key = raw_key.assign(date_added=parsed_dates["date_added"])
        add_issue(
            "duplicate_normalized_key",
            "Multiple rows have the same symbol and parsed date_added value.",
            mask=eligible
            & parsed_dates["date_added"].notna()
            & normalized_key.duplicated(keep=False),
        )

    return AuditResult(data=data, report=report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path", type=Path, help="Local membership CSV; no download occurs."
    )
    args = parser.parse_args(argv)
    try:
        result = audit_membership(load_membership_csv(args.path))
    except (OSError, UnicodeError, ValueError, csv.Error, pd.errors.ParserError) as exc:
        print(f"Cannot audit CSV: {exc}", file=sys.stderr)
        return 2
    print(result.report.to_json())
    return 0 if result.report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
