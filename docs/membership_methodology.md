# Membership queries and source events

`indexlens.membership` operates on audited CSV history in memory. It performs no network calls. [exploratory_data_audit.md](../exploratory_data_audit/exploratory_data_audit.md) documents the reviewed source dataset.

## Public functions

| Function | Result |
|---|---|
| `prepare_history(audit_result, *, source, dataset_sha256, coverage_start, coverage_end)` | Copied audited rows with source metadata and record positions |
| `membership_at(history, as_of)` | Selected source rows for a date, metadata, audit issues, and dated ambiguity warnings |
| `membership_keys(snapshot)` | Distinct `(symbol, cik)` pairs |
| `compare_snapshots(earlier, later)` | Appeared, disappeared, and unchanged identity sets with independent copies of both snapshots |
| `build_source_events(history)` | Sorted ADD/REMOVE events, source evidence, metadata, and source audit issues |

Pass a valid audit in original source order to `prepare_history()`, before sorting or filtering. Compute the hash from the matching CSV bytes and supply reviewed coverage boundaries. The helper does not verify the hash or infer coverage. Rebuild the audit and hash when the input changes; treat prepared history as the pinned input to subsequent queries.

Original values, including CIK strings with leading zeros, remain unchanged. Approximate dates and nonblocking audit findings remain available for inspection.

## Example

After installing the project, run this code in Python. It uses no external file or credentials.

```python
from datetime import date
from hashlib import sha256
from io import StringIO

import pandas as pd

from indexlens.data_audit import audit_membership
from indexlens.membership import (
    build_source_events,
    compare_snapshots,
    membership_at,
    membership_keys,
    prepare_history,
)

csv_text = """symbol,cik,name,sector,date_added,date_removed,created_at
ALFA,0001,Alpha Company,industrials,2025-12-15,,2026-01-01
BETA,0002,Beta Company,industrials,2026-01-05,2026-01-10,2026-01-05
BETA,0002,Beta Company,industrials,2026-01-20,,2026-01-20
"""
audit_result = audit_membership(pd.read_csv(StringIO(csv_text), dtype="string"))
history = prepare_history(
    audit_result,
    source="synthetic-example",
    dataset_sha256=sha256(csv_text.encode("utf-8")).hexdigest(),
    coverage_start=date(2026, 1, 1),
    coverage_end=date(2026, 1, 31),
)

earlier = membership_at(history, date(2026, 1, 6))
later = membership_at(history, date(2026, 1, 21))
difference = compare_snapshots(earlier, later)
event_table = build_source_events(history)

print(sorted(difference.unchanged))
print(event_table.data[["symbol", "event_date", "event_type"]].to_string(index=False))
```

The unchanged identities are `[("ALFA", "0001"), ("BETA", "0002")]`. Appeared and disappeared sets are empty. The event table records BETA's ADD on January 5, REMOVE on January 10, and ADD on January 20. Endpoint comparison alone cannot reveal the intervening removal and re-entry.

## Membership rules

For a query date `d`, all three conditions must hold:

```text
date_added_parsed <= d
AND (date_removed_parsed is missing OR date_removed_parsed > d)
AND created_at_parsed <= d
```

Addition is inclusive and removal is exclusive. Equal addition/removal dates yield no active interval. Missing removal is open-ended only within reviewed coverage. `created_at` is an additional source identity guard; it does not establish when all information became available.

Queries include both coverage boundaries. An empty snapshot is a valid result with no selected rows. A query outside coverage raises an error.

## Identity, counts, and issues

`(symbol, cik)` identifies a source identity, not a permanent real-world security. Repeated intervals remain separate source rows. Do not merge symbols that share a CIK or CIKs that share a symbol.

These counts can differ:

```python
snapshot = later
row_count = len(snapshot.data)
identity_count = len(membership_keys(snapshot))
symbol_count = snapshot.data["symbol"].nunique()
```

Snapshots copy source-wide audit issues and add these nonblocking warnings when applicable:

| Issue type | Meaning |
|---|---|
| `duplicate_active_identity` | Multiple selected rows represent the same `(symbol, cik)` on the query date |
| `multiple_active_ciks` | A selected symbol has more than one active CIK on the query date |

Warnings retain every selected row. Their `row_count` counts all participating rows, and their details identify the query date. Nonoverlapping intervals do not trigger these warnings. Snapshot comparisons retain each endpoint's issues. The in-memory and PostgreSQL snapshot functions share the same warning builder.

Source event tables retain source-wide audit issues, including equal-endpoint findings, even when no event rows fall in coverage. Event-specific approximation and `created_after_event` flags are columns in `event_table.data`.

## Source events and evidence

The event window is `(coverage_start, coverage_end]`. The start date is the baseline; additions before or on it do not become new events. ADD rows use the recorded addition date and its approximation flag. REMOVE rows use nonmissing recorded removal dates and their corresponding flags. Both events are retained for equal endpoints within the window.

`created_after_event` is true when `created_at` is later than the nominal event date. The date remains unchanged. Approximation flags and source-date relationships do not independently establish official accuracy or information availability. Guarded membership snapshots cannot generally be reconstructed solely from nominal source events.

Events sort by date, symbol, CIK, event type, and source record position. Same-date ordering is presentation only. Empty results retain the event columns and metadata.

`source_record_position` is a zero-based position assigned before filtering. Together with `dataset_sha256`, it identifies a record in that input version. It is not a DataFrame index label or a physical CSV line number.

The event table's `source_data` contains copies of all audited source rows, including original fields. Retrieve supporting rows by the position column:

```python
position = event_table.data.iloc[0]["source_record_position"]
source_record = event_table.source_data.loc[
    event_table.source_data["source_record_position"] == position
]
```

Use this lookup only when the event table is nonempty. Returned data and issues are independent copies of the input history; edits to a result do not update that history.

## Errors and limits

- Preparation rejects failed audits and reversed coverage boundaries.
- Membership queries reject dates outside coverage.
- Snapshot comparison rejects reversed dates and differing source labels, dataset hashes, or coverage boundaries. Equal dates are allowed.
- Comparison results are sets; sort them for presentation. Unchanged means present at both endpoints, not continuous membership between them.

Checking which rows qualify for a membership snapshot takes O(h) time, because all h history rows must be checked. Building and comparing identity sets for snapshots with n and m rows takes expected O(n + m) time and space. Sorting k event rows takes O(k log k) time.
