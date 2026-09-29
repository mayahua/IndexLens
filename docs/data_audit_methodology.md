# Membership CSV validation and audit reports

`indexlens.data_audit` loads local membership-history CSVs and checks their structure, values, dates, and repeated records. It returns a copy of the input with parsed dates, approximation flags, and an audit report. The [exploratory audit](../exploratory_data_audit/exploratory_data_audit.md) documents the reviewed source dataset and its findings.

## Public functions

| Function | Result |
|---|---|
| `load_membership_csv(path)` | A DataFrame loaded as text, preserving identifiers such as CIKs with leading zeros |
| `parse_date_column(values, *, allow_approximate)` | Three Series: parsed dates, approximation flags, and malformed-date flags |
| `audit_membership(frame)` | An `AuditResult` containing `data` and `report` |
| `report.to_json()` | An indented JSON string containing `is_valid` and all report fields |

Use `load_membership_csv()` before auditing a file. An existing DataFrame can be passed directly to `audit_membership()`, but identifiers must already retain their original text. The audit cannot restore leading zeros lost during an earlier numeric conversion.

## Input schema and loading

All seven columns must be present. Column names are case-sensitive.

| Column | Value requirement |
|---|---|
| `symbol` | Required, nonblank text; non-uppercase values are flagged for review |
| `cik` | Required, nonblank text; leading zeros remain significant |
| `name` | Required, nonblank text |
| `sector` | Required, nonblank text |
| `date_added` | Required date; one trailing `*` may mark an approximation |
| `date_removed` | Date or missing value; one trailing `*` may mark an approximation |
| `created_at` | Required date without an approximation marker |

The loader reads UTF-8, accepts a byte-order mark, and handles quoted commas. It requires unique, nonblank column names and the same number of fields in each data record as in the header. Blank lines are skipped.

Empty CSV fields and pandas' default missing-value markers, such as `NA`, become missing values. A literal `NA` identifier would therefore be treated as missing. Whitespace-only fields remain text and are checked separately. Extra columns are retained and reported.

## Example

After installing the project, run this code in Python. It writes a temporary CSV, loads it through the public loader, and audits it. All data is synthetic.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from indexlens.data_audit import audit_membership, load_membership_csv

csv_text = """symbol,cik,name,sector,date_added,date_removed,created_at
ALFA,0001,Alpha Company,industrials,2025-12-15*,,2026-01-01
BETA,0002,Beta Company,industrials,2026-01-05,2026-01-10,2026-01-05
BETA,0002,Beta Company,industrials,2026-01-20,,2026-01-20
"""

with TemporaryDirectory() as directory:
    path = Path(directory) / "membership.csv"
    path.write_text(csv_text, encoding="utf-8")
    frame = load_membership_csv(path)
    result = audit_membership(frame)

print("Valid:", result.report.is_valid)
print("Rows:", result.report.rows)
print("Missing removal dates:", result.report.missing_counts["date_removed"])
print("Approximate additions:", result.report.approximate_counts["date_added"])
print("CIKs:", result.data["cik"].tolist())
print("Issues:", [issue.issue_type for issue in result.report.issues])
```

Expected output:

```text
Valid: True
Rows: 3
Missing removal dates: 2
Approximate additions: 1
CIKs: ['0001', '0002', '0002']
Issues: ['approximate_date']
```

ALFA's original addition remains `2025-12-15*`; its parsed date is `2025-12-15` and its approximation flag is true. BETA's two intervals remain separate rows. Repeated identifiers and a missing removal date do not fail validation.

## Date and interval rules

Dates must use `YYYY-MM-DD` and represent a valid calendar date. Membership dates also accept one trailing `*`. Values such as `2026-1-05`, `2026-02-30`, `2026-01-05**`, or dates with surrounding whitespace are malformed. An approximation marker on `created_at` is also malformed.

Missing and malformed dates both produce `NaT` in parsed columns. Missing required dates and malformed nonmissing values are reported separately. A malformed removal date is a validation failure, so it must not be treated as an open-ended interval. Approximation flags record the source marker; a marked value can still fail date validation.

For rows with two parseable membership dates:

- Removal before addition fails validation as `reversed_interval`.
- Equal addition and removal dates produce the nonblocking `equal_endpoints` finding.
- Removal after addition passes the interval check.

These checks use nominal dates even when they are approximate. The audit does not infer an uncertainty range or adjust dates. It does not reject a `created_at` later than `date_added`; the membership module applies that relationship when selecting snapshots and flagging source events.

## Output data and report

`result.data` preserves original rows, their order, index labels, values, and extra columns. The input DataFrame is unchanged. For each available date column, the audit appends:

| Added column | Meaning |
|---|---|
| `date_added_parsed` | Parsed nominal addition date |
| `date_removed_parsed` | Parsed nominal removal date |
| `created_at_parsed` | Parsed source creation date |
| `date_added_approximate` | Whether the addition carries an approximation marker |
| `date_removed_approximate` | Whether the removal carries an approximation marker |

Checks use a text representation of the input. The report's `columns` and `dtypes` describe the original DataFrame, before generated columns are added. Missing columns are reported, and checks requiring them are skipped. A failed audit still returns the data and report for inspection.

| Report field | Meaning |
|---|---|
| `is_valid` | Whether no blocking issue types were found |
| `rows`, `columns`, `dtypes` | Original input size, column names, and data types |
| `missing_counts`, `blank_counts` | Missing values and whitespace-only or empty strings per input column |
| `unique_counts`, `repeated_counts` | Distinct nonmissing values and occurrences beyond the first for `symbol`, `cik`, and `sector` |
| `approximate_counts` | Marked values in each available membership-date column |
| `date_ranges` | Earliest and latest parseable dates per available date column, or `None` if none parse |
| `exact_duplicate_rows` | Repeated whole records beyond the first copy, compared across all input columns as text |
| `issues` | Findings with a type, explanation, affected column, and affected-row count |

Each issue is a `ValidationIssue` with `issue_type`, `issue_details`, `column`, and `row_count`. The column is `None` for findings involving a whole record or interval. The row count is `None` for findings that do not refer to particular rows, such as a missing column. Counts are positional, so repeated DataFrame index labels do not inflate them. Findings are aggregated; they do not contain a list of affected row identifiers.

## Blocking issues and review findings

The following issue types make `report.is_valid` false:

| Issue type | Meaning |
|---|---|
| `empty_dataset` | No data records to validate |
| `missing_column` | An expected column is absent, including `date_removed` |
| `missing_required` | A required field is missing |
| `blank_required` | A required field contains empty or whitespace-only text |
| `duplicate_row` | Whole records repeat across all input columns |
| `malformed_date` | A nonmissing date has an invalid format or calendar value |
| `reversed_interval` | A parseable removal date precedes its addition date |

These findings require review but do not independently fail the audit:

| Issue type | Meaning |
|---|---|
| `unexpected_column` | An extra column is retained |
| `surrounding_whitespace` | A value contains leading or trailing whitespace |
| `symbol_case` | A symbol is not uppercase |
| `approximate_date` | A membership date has an approximation marker |
| `equal_endpoints` | Nominal addition and removal dates are equal |
| `duplicate_raw_key` | Multiple rows share a symbol and original `date_added` value |
| `duplicate_normalized_key` | Multiple rows share a symbol and parsed `date_added` value |

For example, two rows with the same symbol and additions `2026-01-05` and `2026-01-05*` have the same normalized key but different raw keys. Here, normalization means parsing the date; symbols and CIKs are not rewritten. These audit keys do not include CIK and do not establish that two rows represent the same security.

Duplicate issue counts include every participating row. Three identical records therefore give `exact_duplicate_rows == 2` and a `duplicate_row` issue with `row_count == 3`. Repeated symbol or CIK counts alone are descriptive: they do not imply invalid duplicates. No fixed symbol-length limit is enforced, so a six-character symbol is allowed.

## Command-line use and errors

From the repository root after installation:

```sh
python -m indexlens.data_audit path/to/components_history.csv
```

| Exit code | Meaning | Output |
|---|---|---|
| `0` | Audit passes, possibly with nonblocking findings | JSON report on standard output |
| `1` | Audit contains blocking issues | JSON report on standard output |
| `2` | The CSV cannot be loaded or audited | Error message on standard error; no audit JSON |

Missing or unreadable files, encoding errors, invalid CSV structure, and ambiguous column layouts follow the error path. Invalid command-line arguments also exit with code `2` and a usage message.

When calling the Python API directly, `audit_membership()` raises `ValueError` for duplicate or non-string column names, or columns reserved for parsed dates and approximation flags. Ordinary validation failures return `AuditResult` with `report.is_valid == False`. Use the original frame when repeating an audit; an audited frame containing generated columns is rejected.

## Interpretation and limitations

A passing audit means the input satisfies these structural and date rules. It does not confirm official index membership, complete historical coverage, permanent security identities, or when the information became available. Date ranges describe the supplied rows; they do not establish supported query coverage. The audit neither corrects source values nor removes flagged records.
