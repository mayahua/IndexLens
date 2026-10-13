# Price ingestion — Yahoo and offline CSV

Step 6 implements shared normalization, evidence storage, offline CSV loading,
Yahoo/yfinance fetching and resumable manifest ingestion. Mapping/basis approval
and feature/model calculations remain later gates. See [database setup](database.md).

## Live Yahoo demonstration

Install `.[dev]`, start PostgreSQL, export `INDEXLENS_DATABASE_URL` and upgrade
migrations using the database guide. No market-data API key is required.
The existing `.env.example` gives the local Compose connection example; commands
read exported variables and do not automatically load `.env`.

```sh
python -m indexlens.prices.fetch demo --dataset-id yahoo-demo-v1 \
  --start 2026-08-01 --end 2026-10-10
```

The command fetches MSFT, JPM, XOM, JNJ and CAT. Start is inclusive; end is
exclusive. Repeating it reuses verified cached responses and existing database
sources. `--offline` forbids new network requests and reports cache misses.
Default cache: `data/cache/yahoo`, relative to the current directory. `--cache`
accepts another path. Keep this directory private: it contains market responses
and a separate yfinance session cache. Authentication bodies, cookies and signed
request URLs are not copied into the retained chart receipts or command output.

## Full research manifest

Supply the reviewed manifest from the source-history preparation. Real source
files and manifests remain private and are not distributed as public fixtures.
There are no machine-specific input paths in the implementation.

```sh
python -m indexlens.prices.fetch research \
  --manifest path/to/manifest.json \
  --dataset-id yahoo-research-full-population-v2
```

The accepted manifest carries `data_kind: real`, the pinned `source_sha256`,
`eligible_source_identity_count`, an `identities` list with text `symbol` and `cik`,
and `price_request_envelope.start_inclusive` / `end_exclusive`. Original identity
fields, record positions and mapping notes are preserved. Declared population
must equal the complete list length; no cap or sampling option is applied.
The reviewed full-population-v2 manifest contains 580 eligible identities and
requests 1 December 2021 through 1 August 2026 exclusive. Existing feature and
outcome boundaries still govern later research eligibility.

Each identity gets an explicit loaded/failed/cancelled/not-attempted result in
`cache/runs/<run-id>/summary.json`; the input manifest is pinned alongside it.
A downloaded history can still be partial. Coverage records first/last dates,
missing XNYS sessions/ranges, duplicates, unexpected dates and numeric issues.
The calendar is a US equity-session reference, not verification of listing dates
or continuous identity. A failed response means unavailable history, never a
synthetic replacement or a negative outcome label.

Yahoo candidate symbols default to the source symbol with dots changed to
hyphens; an explicit `provider_symbol` overrides that candidate. The response
must identify the requested symbol and an equity with a valid exchange timezone.
These checks **do not approve historical identity**. Every result and stored row
keeps mapping/basis review unresolved; no approved security join is created.

## Resume, request controls and failure handling

The default controls are `--concurrency 2`, `--request-interval 1`,
`--request-timeout 15`, `--job-timeout 90`, `--retries 2`, `--backoff 2` and
`--batch-size 500`. Concurrency limits active workers. A shared SQLite gate spaces
underlying HTTP requests, including yfinance metadata/authentication calls, across
workers and commands using the same cache. Request pacing is independent of
concurrency. Workload limits do not reduce the population.

Transient network/timeout/429/5xx failures get bounded exponential-backoff retries;
404 histories are terminal for that run. yfinance's configurable internal retries
are disabled; its authentication fallback requests remain paced and bounded by the
worker timeout. Each attempt retains chart bytes and a receipt. Successful cache
reads verify response/receipt hashes and regenerated logical CSV bytes. Corrupt
cache evidence fails explicitly; it is not silently overwritten or downloaded
again. Preserve it and use a separate cache location to investigate a fresh fetch.

Previously failed transport requests stay cached. Use `--retry-failed` to make a
new bounded attempt; it does not refresh received responses. A received response
rejected by mapping or shape checks stays pinned: review the candidate symbol or
use a separate cache for an explicit new retrieval. Malformed chart containers are
reported as `chart_shape_invalid` for the affected identity; the remaining
identities are still processed. A later deliberate refresh can
use another cache directory with the same dataset ID, preserving all versions and
letting the database flag changed prices as conflicts. Changing adjustment settings
requires a distinct basis or dataset.

Ctrl-C cancels queued work, kills/reaps active provider subprocesses, and lets any
current atomic database load close its session. Repeated cancellation requests
still wait for that load to finish or roll back before the final report is written;
a load failure during cleanup does not restart queued work. Completed sources and
cache entries remain reusable. The interrupted run records unfinished identities.
Rerun the same command to resume; a response saved before database interruption is loaded from cache.
The command logs symbol/status/counts without exception bodies or credentials.
Exit codes: 0 all identities loaded, 2 recorded identity failures, 1 invalid setup,
130 cancellation. Row-value issues and missing sessions remain visible even when
loading succeeds. Logs and reports describe ingestion, not feature readiness.

## Run the synthetic example

From the repository root, install `.[dev]`, start Compose and export
`INDEXLENS_DATABASE_URL` as described in the database guide, then run:

```sh
python -m alembic upgrade head
python -m indexlens.prices tests/fixtures/prices_synthetic.csv \
  --metadata tests/fixtures/prices_synthetic.metadata.json --batch-size 2
```

This deliberately imperfect synthetic fixture has seven records: four distinct
observations, one equal duplicate, one conflicting row and one rejected key.
It also illustrates missing volume, zero volume and impossible OHLC. Dates are
invented 2026 examples. Run the command again: `source_id` stays the same and
`inserted` becomes `false`. Counts on a repeat describe the original load.

The command returns JSON counts and the source SHA-256, or a nonzero exit status
with a generic error that does not print input values or connection credentials.
Row validation issues are retained results, not transaction failures; inspect
`price_rows.issues` and `price_observations.conflicted`. A successful exit means
the evidence was stored, not that every row is usable. Empty, header-only inputs
are recorded with zero counts. Malformed CSV structure, conflicting declarations,
changed settings for the same series, or a database failure roll back the load.

## Input contract

CSV headers use the logical names `provider_symbol`, `observation_date`, `open`,
`high`, `low`, `close`, `volume`. The two key columns are required; absent numeric
columns and blank cells become missing values with issues. Dates must be exactly
`YYYY-MM-DD`. Extra columns, including original Close/Adj Close, corporate actions,
source identity and mapping claims, remain in original row evidence. The Yahoo adapter translates saved chart responses into this same contract.
The offline CSV command accepts these logical headers, not arbitrary exports.

The required JSON manifest fields are:

| Field | Declaration |
|---|---|
| `dataset_id` | Stable namespace reused across files and downloads |
| `data_kind` | `real` or `synthetic`; cannot change within a namespace |
| `provider` | Original provider, e.g. `yahoo`; CSV is only the transport |
| `price_basis` | Explicit returned basis; Yahoo's raw basis remains unverified |
| `retrieved_at` | Original retrieval time with timezone, stored as UTC |
| `source_reference` | Stable evidence label, without credentials or signed URLs |
| `adjustment_settings` | Explicit JSON object containing applicable provider controls |
| `source_notes` | Source/terms notes, review date and redistribution status |

Additional manifest declarations are preserved. `normalization_version` is
recorded as `price-v1`. The loader fingerprints canonical manifest JSON as well
as the exact CSV bytes. It does not infer historical availability from retrieval
time, establish the truth of caller declarations, or approve mappings. Any shared
identity/kind/provider/basis values included in a CSV must match the manifest.
Keep real data and its manifests private; do not put credentials in either.

Yahoo evidence must retain explicit `auto_adjust`, `back_adjust`, `repair`,
`actions`, `keepna` and `rounding` settings as applicable. The existing sample
used adjustments disabled and retained both Close and Adj Close. That does not
establish original as-traded OHLC. Changing adjustment settings within the same
dataset/provider/basis is rejected; declare a distinct basis or dataset instead.

## Tables and validation

Migration `0002` adds four tables; `0001` and membership semantics are unchanged.

| Table | Meaning |
|---|---|
| `price_datasets` | A stable namespace with one immutable real/synthetic declaration |
| `price_sources` | Exact input bytes, SHA-256, retrieval time, manifest, fingerprint and load counts |
| `price_observations` | One logical key and its current conflict/numeric-usability state |
| `price_rows` | Every original record, its hash, zero-based position, normalized candidate, issues and optional observation link |

Normalized OHLCV values are exact decimal strings in JSONB, or null. For example,
`100.00` and `100` both normalize to `1e2`. This avoids binary float rounding and
an arbitrary fixed database scale; consumers must parse these as decimals before
calculations. Original strings remain unchanged in `original_fields`; the exact
CSV remains in `source_bytes`. Record hashes cover canonical original-field JSON,
not physical CSV lines.

Prices must be finite and positive; complete OHLC bars must satisfy their high/low
relationships. Volume must be a finite nonnegative integer. Missing volume is
null with `missing_volume`; zero stays zero. Malformed/nonfinite inputs have
distinct issue codes. Invalid keys produce source rows without observations.
Invalid or incomplete keyed bars remain dated observations so later windows
cannot silently replace them with older bars. Invalid OHLC bars block both numeric usability flags;
missing prices only block dependent price calculations. Nothing is filled or forward-filled.

`prices_usable` and `volume_usable` describe numeric checks and conflict state
only. **They are not feature-join approval.** Every source row carries
`unresolved_mapping` and `unverified_price_basis`; this increment has no approved
security mapping or feature join. Mapping/security claims are evidence only.
Later features must also enforce dated mapping, basis review and complete windows.

## Duplicates, conflicts and transactions

The unique observation key is
`(dataset_id, provider, provider_symbol, observation_date, price_basis)`.
Retrieval time, file hash and batch number never create new observations.

Equal normalized OHLCV values share one observation while retaining every row.
Different values or null-versus-value create a persistent conflict. Both candidates
and their provenance remain stored. The observation's first candidate is retained
for comparison, **not selected as a winner**; a conflicted observation has both
usability flags false. Later repeats cannot clear a conflict or fill an earlier
null. Resolution is deferred to an explicit future workflow. Row issues describe
validation at load time; always join the current observation to detect conflicts
introduced by a later file.

Same bytes plus the same normalized manifest return the existing source. A new
retrieval manifest adds provenance without adding observation identities. Dataset
locks serialize concurrent loads within one namespace; database unique constraints
and foreign keys reinforce identity and cross-dataset evidence isolation.

Each load owns one session and transaction. Failures roll back source rows,
observations and changes to existing conflict flags. Parsing uses a real batch
iterator, handles final partial batches, and flushes/releases ORM objects at each
boundary. The exact bytes and decoded input are materialized: this is not a
memory-bounded streaming pipeline or a measured bulk-loading performance claim.

## Provider evidence and verification

The adapter archives Yahoo chart response bytes and safe retrieval receipts before
normalization. It reads OHLCV from the raw response, retaining missing volume even
where yfinance's returned DataFrame fills it with zero. Original Adj Close,
timestamps and corporate actions remain separate evidence; exchange-local dates
identify daily bars. The yfinance CSV is saved for comparison only. Response
hashes and relative cache references are retained in each database source manifest.
[The yfinance history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)
documents its adjustment, missing-value and timeout controls.

The database guide's test commands include this package. Tests cover independent
HTTP/CSV fixture equivalence, calendar gaps, mapping mismatches, all 580 manifest
entries, cache reuse/tampering, transient and permanent failures, bounded retries,
concurrency versus pacing, process timeout/cancellation, and transaction behavior.
Ordinary tests use synthetic 2026 data and mocked provider workers. Live requests
and clean source-export checks are recorded separately in the private Step 6 notes.
Remote CI execution is not implied by local checks.

Step 6 provides ingestion and provenance. Full usable research coverage is still
limited by real provider gaps and pending mapping/basis review. Step 7 must enforce
complete observed-session windows and approved joins. Features, 2022–2024 training,
January 2025–July 2026 testing, models and later OpenAI integration remain unfinished.
