# Price ingestion — Yahoo and offline CSV

IndexLens downloads daily prices from Yahoo through yfinance or loads saved CSV files. Both routes convert values to a shared format, validate them and preserve the original inputs in PostgreSQL. Downloads can resume using saved responses. See [database setup](database.md) for installation and [features and momentum ranking](features.md) for calculations using reviewed prices.

## Yahoo demo command

Follow the database guide to install `.[dev]`, start PostgreSQL, export `INDEXLENS_DATABASE_URL` and upgrade migrations. No market-data API key is required. Commands read exported variables and do not automatically load `.env`.

```sh
python -m indexlens.prices.fetch demo --dataset-id example-yahoo-demo-v1 \
  --start 2026-04-01 --end 2026-06-01
```

The example makes real Yahoo requests for five fixed symbols: MSFT, JPM, XOM, JNJ and CAT. Its dataset name and dates are illustrative. The start date is included; the end date is excluded. Repeating the command reuses verified cached responses and existing database sources. `--offline` prevents network requests and reports missing cached responses as failures.

The default cache is `data/cache/yahoo`, relative to the current directory; `--cache` selects another location. It stores market responses for reuse and a separate yfinance session cache. Chart receipts record retrieval details. Authentication responses, cookies and signed request URLs are excluded from these receipts and command output.

## Manifest ingestion

The `research` subcommand reads a manifest: a JSON file listing source identities and the requested date range. Supply its path and a stable dataset ID. The dataset name below is illustrative.

```sh
python -m indexlens.prices.fetch research \
  --manifest path/to/manifest.json \
  --dataset-id example-yahoo-manifest-v1
```

The manifest requires `data_kind: real`, the source file's `source_sha256` hash, `eligible_source_identity_count`, an `identities` list containing text `symbol` and `cik` values, and `price_request_envelope.start_inclusive` / `end_exclusive`. Original identity fields, record positions and mapping notes are preserved. The declared identity count must match the list length. The command processes the whole list without sampling or imposing a maximum number of identities.

Each identity receives a loaded/failed/cancelled/not-attempted result in `runs/<run-id>/summary.json` under the cache directory, alongside a saved copy of the manifest. Loaded histories may still be incomplete. Coverage reports list first/last dates, missing trading sessions and ranges, duplicates, unexpected dates and numeric issues. The New York Stock Exchange calendar (`XNYS`) provides a reference for US equity sessions; it does not confirm listing dates or historical identity. Failed downloads remain unavailable and are never replaced with synthetic data.

Yahoo candidate symbols default to the source symbol with dots changed to hyphens; `provider_symbol` overrides this choice. Responses must identify the requested symbol as an equity with a valid exchange timezone. These checks **do not approve historical identity**. Results and stored rows retain unresolved mapping and price-basis status. Mapping review checks which security a provider symbol represents over specified dates; price-basis review checks the series and its adjustments. Both require separate approval before feature calculations.

## Resume, request controls and failure handling

The following options control downloads and database loading:

| Option | Default | Meaning |
|---|---|---|
| `--concurrency` | `2` | Maximum active download workers |
| `--request-interval` | `1` | Minimum seconds between HTTP requests |
| `--request-timeout` | `15` | Timeout in seconds for each HTTP request |
| `--job-timeout` | `90` | Maximum seconds for one download attempt |
| `--retries` | `2` | Additional attempts for retryable failures |
| `--backoff` | `2` | Seconds before the first retry; the wait doubles on each retry |
| `--batch-size` | `500` | CSV rows processed in each database batch |

A shared SQLite file coordinates request timing across workers and commands using the same cache, including yfinance metadata and authentication requests. Concurrency does not override request spacing. These controls do not reduce the identity list.

Recognised network errors, timeouts, HTTP 429 (rate limiting), and server errors 500, 502, 503 and 504 can be retried up to the configured limit. A chart response with HTTP 404 is not retried during that run. yfinance's configurable internal retries are disabled; its alternative authentication requests still follow the request spacing and timeout limits. Each attempt saves a receipt and any chart responses received.

Cache reuse checks response and receipt hashes and verifies that rebuilding the CSV produces the same contents. A damaged or changed entry fails explicitly, without automatic replacement. Preserve it and use a separate cache directory for a fresh download.

Failed download attempts stay cached. `--retry-failed` retries them within the configured limits; it does not refresh received responses. Responses rejected by identity or structure checks remain saved: review the candidate symbol or use a separate cache for a new request. Invalid chart structures produce `chart_shape_invalid` for the affected identity; processing continues for the others.

To deliberately refresh prices, use another cache directory with the same dataset ID. This preserves earlier versions and allows the database to flag changed values as conflicts. Changing adjustment settings requires a distinct price basis or dataset.

Ctrl-C cancels queued work and stops active download processes. A running database load must finish or undo its changes before the final report is written, even after repeated cancellation. A load failure during cancellation does not restart queued work. Completed sources and cache entries remain reusable; unfinished identities are recorded. Rerun the command to resume, reusing responses saved before a database interruption.

Logs show symbols, statuses and counts without full exception messages or credentials. Exit codes are `0` when all identities load, `2` when identity failures are recorded, `1` for invalid setup and `130` for cancellation. Numeric issues and missing sessions remain visible even when loading succeeds; inspect them before deciding whether the prices can support features.

## Run the synthetic example

From the repository root, follow the database guide to install `.[dev]`, start Compose and export `INDEXLENS_DATABASE_URL`, then run:

```sh
python -m alembic upgrade head
python -m indexlens.prices tests/fixtures/prices_synthetic.csv \
  --metadata tests/fixtures/prices_synthetic.metadata.json --batch-size 2
```

This synthetic example contains seven records: four distinct observations, one equal duplicate, one conflicting row and one row with an invalid symbol or date. It includes missing volume, zero volume and inconsistent opening, high, low and closing prices (OHLC). All dates are invented 2026 examples. Repeating the command keeps the same `source_id` and returns `inserted: false`; the counts describe the original load.

The command returns JSON counts and the source SHA-256 hash, or a nonzero exit code with a generic error that omits input values and database credentials. Numeric issues remain with saved rows; inspect `price_rows.issues` and `price_observations.conflicted`. Successful loading does not make every row usable. Valid header-only CSV files are saved with zero counts. Malformed CSV structure, conflicting declarations, changed settings for the same series or a database failure undo the whole load.

## Input contract

CSV columns are `provider_symbol`, `observation_date`, `open`, `high`, `low`, `close` and `volume`. Symbol and date columns are required; dates must use exactly `YYYY-MM-DD`. Missing numeric columns and blank cells become missing values with recorded issues. Extra columns, including original Close/Adj Close, corporate actions, source identities and mapping claims, remain with the original rows. Yahoo responses are converted to this format; other CSV exports must use these column names before loading.

The `--metadata` JSON file describes the saved prices, while the download manifest above lists what to fetch. Required metadata fields are:

| Field | Meaning |
|---|---|
| `dataset_id` | Stable dataset name reused across related files and downloads |
| `data_kind` | `real` or `synthetic`; cannot change for the same dataset ID |
| `provider` | Original data provider, such as `yahoo`, even when loading from CSV |
| `price_basis` | Explicit name for the price series and its adjustment basis; Yahoo's returned basis remains unverified |
| `retrieved_at` | Original retrieval time with a timezone, stored in UTC |
| `source_reference` | Stable reference to the source evidence, without credentials or signed URLs |
| `adjustment_settings` | JSON object recording the provider settings used |
| `source_notes` | Notes on the source and its terms, review date and redistribution status |

Additional metadata fields are preserved, and `normalization_version` is recorded as `price-v1`. Separate SHA-256 hashes identify the exact CSV contents and validated metadata written in a consistent JSON format. The loader does not confirm declarations, approve mappings or infer historical availability from retrieval time. Dataset, real/synthetic, provider or price-basis fields repeated in the CSV must match the metadata. Do not include credentials in either file.

Yahoo data must retain explicit `auto_adjust`, `back_adjust`, `repair`, `actions`, `keepna` and `rounding` settings where applicable. IndexLens disables `auto_adjust`, `back_adjust`, `repair` and `rounding`, and preserves both Close and Adj Close. These settings do not prove that the returned OHLC values are the prices originally traded. Changing adjustment settings within the same dataset, provider and price basis is rejected; use a distinct basis or dataset instead.

## Tables and validation

Migration `0002` adds four tables. Migration `0001` and membership rules are unchanged.

| Table | Each row represents |
|---|---|
| `price_datasets` | A dataset ID with a fixed real/synthetic declaration |
| `price_sources` | One saved input, including its original contents, retrieval time, metadata, hashes and load counts |
| `price_observations` | One observation identified by the fields below, with its current conflict and usability flags |
| `price_rows` | One original record, its hash, position counted from zero, converted values, issues and link to an observation when its symbol and date are valid |

Opening, high, low and closing prices plus volume (OHLCV) are stored as exact decimal strings, or `null`, in PostgreSQL JSONB. Equivalent values share one representation: `100.00` and `100` become `1e2`. This avoids floating-point rounding and fixed decimal-place limits; read these strings as decimals before calculating. Original strings remain in `original_fields`, and the unchanged CSV in `source_bytes`. Row hashes cover original fields written in a consistent JSON format, excluding CSV line formatting.

Prices must be finite and greater than zero. When all four prices are present, the high must be no lower than the other prices, and the low no higher. Volume must be a finite, nonnegative whole number. Missing volume is stored as `null` with `missing_volume`; zero remains zero. Invalid numbers and nonfinite values, such as infinity, receive distinct issue codes.

Rows with an invalid symbol or date are saved without an observation link. Rows with a valid symbol and date remain dated observations even when numeric values are missing or invalid. This preserves their places in feature windows so older observations cannot silently replace them. Missing values are never filled with zero or copied from an earlier observation.

`prices_usable` requires all four OHLC prices to be present and valid. Invalid prices or an impossible high/low range also block `volume_usable`; missing prices alone do not block valid volume. Conflicts make both flags false. These flags describe numeric checks only: **they do not approve data for feature calculations**. Closing-price features apply their own required-value checks, as described in the [feature guide](features.md).

Every source row retains `unresolved_mapping` and `unverified_price_basis`. Proposed security mappings are saved as evidence. Features require a separate review identifying sources by hash and approving security mappings and the price basis for every input date, plus a complete window. That review does not change the original source flags.

## Duplicates, conflicts and transactions

An observation is identified by `(dataset_id, provider, provider_symbol, observation_date, price_basis)`. Retrieval time, file hash and batch number do not create additional observations with the same identifying values.

Rows with equal converted OHLCV values share one observation, while every original row is retained. Different values, including a missing value versus a number, create a persistent conflict. Both versions and their source details remain saved. The first set of values stays on the observation for comparison; a conflict does not choose a preferred version, and both usability flags become false. Later repeats cannot clear the conflict or fill an earlier missing value. The loader does not resolve conflicts. Row issues describe checks at load time, so code must also check the linked observation's current conflict flag to detect disagreements introduced by later files.

The same CSV contents and validated metadata return the existing source. New retrieval metadata adds a source record without duplicating existing observations. Loads for the same dataset wait for one another. Database rules prevent duplicate sources and observation keys, and prevent links between different datasets.

Each load uses its own database session and saves all changes together. Failure undoes new rows, observations and changes to existing conflict flags. The loader processes batches, including smaller final batches, and releases their Python objects after writing pending records. Complete file contents and decoded text remain in memory, so batching does not impose a fixed memory limit. Large-file loading performance has not been benchmarked.

## Provider evidence and verification

IndexLens saves original Yahoo chart responses and retrieval receipts before converting values. It reads OHLCV directly from those responses, preserving missing volume even where yfinance replaces it with zero. Original Adj Close, timestamps and corporate actions remain separate evidence. Daily dates use the exchange timezone. The yfinance CSV is saved for comparison only. Source metadata retains response hashes and relative cache paths. The [yfinance history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py) defines its adjustment, missing-value and timeout controls.

The [database guide's test commands](database.md#checks) include the price code. Tests check that equivalent HTTP and CSV examples produce the same values, and cover calendar gaps, mismatched symbols, processing the full identity list, cache reuse and alteration, temporary and permanent failures, retry limits, request spacing, timeouts, cancellation and undoing failed loads. Provider tests use synthetic examples and simulated download workers. PostgreSQL tests use separate schemas so application tables are preserved.

Loading preserves source evidence and reports data quality. The [feature guide](features.md) explains the additional approvals and complete observation windows required for calculations.
