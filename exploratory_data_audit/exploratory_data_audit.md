# Exploratory S&P 500 membership history data audit

Audit date: **23 September 2026**. Results describe the upstream revision identified below. Later downloads may differ.

The [notebook](exploratory_data_audit.ipynb) contains the calculations and local inspection tables. Counts below are observations about this file only.

## Input and provenance

| Item | Recorded value |
|---|---|
| Description | S&P 500 historical components dataset |
| Local file directory | `data/raw/components_history.csv` |
| SHA-256 | `7b9a8f358cdfc83b853f44c9801be40ff0d300f4e6c09407f48f7e5cffb9f2ad` |
| Rows / columns | 1,002 / 7 |
| Source repository | [lawcal/sp500-components-history](https://github.com/lawcal/sp500-components-history), Calvin Law |
| Dataset version | 21 September 2026, 15:09:50 UTC ([commit](https://github.com/lawcal/sp500-components-history/commit/976bfc1fcfc8503dd369bcaee2346aae47baaf7f)) · [Repository snapshot](https://github.com/lawcal/sp500-components-history/commit/2e59b86998a119d68e377f9f98aa7a816cfc7d5b) · [Download CSV](https://raw.githubusercontent.com/lawcal/sp500-components-history/2e59b86998a119d68e377f9f98aa7a816cfc7d5b/data/components_history.csv) |
| Repository license | [MIT, copyright 2025 Calvin Law](https://github.com/lawcal/sp500-components-history/blob/2e59b86998a119d68e377f9f98aa7a816cfc7d5b/LICENSE); underlying data is derived from Wikipedia |

## Source construction and conventions

The [upstream documentation](https://github.com/lawcal/sp500-components-history/blob/2e59b86998a119d68e377f9f98aa7a816cfc7d5b/README.md) describes daily checks of Wikipedia
revisions and historical coverage beginning **2007-03-05**. Earlier queries are
unsupported. Addition-date tracking began in 2012; unavailable effective dates can
be approximated using revision dates converted to New York time. CIKs missing
from earlier revisions were backfilled using EDGAR and QuantumOnline.

The [implementation](https://github.com/lawcal/sp500-components-history/blob/2e59b86998a119d68e377f9f98aa7a816cfc7d5b/bin/update.py) serializes approximate dates with a
trailing `*`. New symbol records receive a `created_at` based on the processed
revision date. This helps exclude later ticker identities from earlier queries;
it does not establish when every stored field became available.

Upstream normalizes ticker aliases, skips certain malformed revisions, and uses
heuristics to remove short-lived errors. It treats different symbols independently,
rather than modelling ticker changes; overlapping histories can remain. These are
source limitations, not guarantees of official index accuracy.

The repository declares an MIT license. Its Wikipedia-derived data has separate
source provenance; raw datasets remain excluded from this repository.

## Method and verified findings

The notebook reads columns as Pandas strings to retain non-null identifier
text, using Pandas' default missing-value recognition. Each run downloads the source CSV to the local data folder; audit calculations leave loaded values unchanged. 
Date parsing uses `YYYY-MM-DD`, after removing one trailing `*` 
from membership dates into separate working columns. Approximation flags are retained. 
Invalid non-null date strings are counted separately from missing inputs. 
Duplicate and repeat counts exclude the first occurrence, e.g., `A, A, A, B` contains two repeats.

| Check | Observed result |
|---|---:|
| Missing expected columns | 0 |
| Missing required-field values | 0 |
| Blank required-field values | 0 |
| Values with surrounding whitespace across the seven fields | 0 |
| Non-uppercase symbols | 0 |
| Missing `date_removed` values | 503 |
| Exact duplicate rows | 0 |
| Repeated symbols | 34 |
| Repeated CIKs | 106 |
| Duplicate raw `(symbol, date_added)` pairs | 0 |
| Duplicate raw `(cik, symbol, date_added)` triples | 0 |
| Unique symbols / CIKs / sectors | 968 / 896 / 11 |
| Approximate addition dates | 257 |
| Approximate removal dates | 128 |
| Non-null date parsing failures after marker cleanup | 0 |
| Nominal removal dates earlier than addition dates | 0 |
| Equal addition and removal dates | 0 |

Expected columns are `symbol`, `cik`, `name`, `sector`, `date_added`,
`date_removed`, and `created_at`. All except `date_removed` are candidate required
fields. Symbol lengths range from one to six characters; a six-character symbol
must not be rejected solely because of its length.

Repeated symbols and CIKs warrant interpretation, not automatic deletion. A CIK
must not be assumed to identify a unique security. Composite uniqueness above is
measured using raw date text; uniqueness after date normalization requires a separate policy.

### Observed date ranges

| Field | Earliest | Latest |
|---|---|---|
| `date_added` | 1957-03-04 | 2026-09-21 |
| `date_removed` | 2007-03-30 | 2026-09-20 |
| `created_at` | 2007-03-05 | 2026-09-20 |

These extrema do not establish complete coverage, historical information
availability, or an exact effective date for an approximate record. A missing
removal date is not evidence that the record remains active today.

### Initial-date investigation: 500 versus 490

Exactly **500 records** share the earliest `created_at`, 2007-03-05. The notebook
applies the dated-query conditions used by upstream
[`list_components()`](https://github.com/lawcal/sp500-components-history/blob/2e59b86998a119d68e377f9f98aa7a816cfc7d5b/bin/update.py#L1082-L1095):

- Addition on or before that date.
- Removal missing in the original input, or strictly after that date.
- `created_at` on or before that date.

**490 rows** match. All **10 excluded rows** in the initial group have addition
dates later than the anchor. No initial-group rows are excluded because removal
is on/before the anchor or because of missing/malformed addition dates or
malformed removal dates. This explains the arithmetic, not the source semantics.

Of the 490 selected rows, **228 carry at least one approximate membership date**.
The calculation therefore uses nominal dates for uncertain records. Its inclusive
addition/exclusive removal convention and `created_at` condition match the source
helper, but source agreement is not independent verification of historical accuracy.

## Validation rules and unresolved decisions

Candidate validation rules:

- Require the expected columns and non-missing, non-blank required values.
- Distinguish permitted missing removals from malformed non-null dates.
- Keep raw date text, parsed dates, and approximation flags.
- Flag exact duplicate rows and reversed nominal intervals.
- Allow repeated symbols and CIKs; review source-specific composite uniqueness.
- Report text normalization issues before deciding whether to normalize them.

Resolve or explicitly document these before claiming historical correctness:

1. Remaining source errors and the implications of backfilled or later-corrected fields.
2. Information-availability claims beyond the documented `created_at` convention.
3. Why ten records in the initial group have later addition dates.
4. Membership boundaries, approximate-date handling, and unsupported query dates.
5. Equal endpoints and duplicate keys after date normalization.

## Refresh and reproduction

Select the `indexlens` kernel, use the project root or `data audit/` as the working
directory, and run all cells in order. The first code cell downloads the historical
CSV from upstream `main`, saves it to `data/raw/components_history.csv`, and reads
it with Pandas. Each run replaces the local CSV. No API key is needed.

The request has a 30-second timeout and reports HTTP errors. Internet access is
required. For a local-only audit, skip the download statements and run
`df = pd.read_csv(DATA_PATH, dtype="string")` against an existing file.

For the exact dataset described here, replace `SOURCE_URL` with the pinned download
URL in the provenance table. The dataset update date is the committer timestamp
of the latest commit affecting the historical CSV at the audited revision.

The notebook calculates counts on every run. This document remains a dated record
and does not update itself. Saved notebook outputs are empty; inspection cells
show source records when executed.
