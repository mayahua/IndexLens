# Public API

The API reads membership, events and prices from PostgreSQL, calculates dated price
and benchmark financial context, and serves saved nine-feature removal research.
The independent `observed-20-v2` feature endpoint supports momentum rankings.
Responses retain real/synthetic labels, supported dates, source hashes and unavailable-value reasons.

An **endpoint** is an API operation identified by its HTTP method and URL path, such as `GET /api/v1/indices`. Data endpoints use `GET`; the explanation endpoint uses `POST`. All have the `/api/v1` prefix. A **route** connects an endpoint to the Python function that handles its requests.

Swagger UI provides interactive documentation at `/docs`. Its OpenAPI schema, the machine-readable description of the endpoints and their parameters, is at `/openapi.json`.

A complete portable synthetic workflow is available in [reproducible delivery](delivery.md).

## Start locally

Follow [database setup](database.md), install `.[dev]`, apply the existing migrations,
and load the desired membership and price datasets. No additional migration is required.
Set `INDEXLENS_DATABASE_URL` as described there. Create an API configuration file:

```json
{
  "indices": {
    "sample": {"version_id": 1, "label": "Synthetic example"}
  },
  "reviews": {},
  "runs": {}
}
```

Replace `1` with the source version ID returned by the membership loader. The ID and
label above are illustrative. An index entry publishes one specific source version;
multiple versions can have separate index identifiers. There is no implicit latest
version. Empty `reviews` and `runs` allow membership-only use.

```sh
export INDEXLENS_API_CONFIG='path/to/api.json'
python -m uvicorn indexlens.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Settings use exported environment variables. The application does not load `.env`.
The configuration file's directory is the base for relative file paths. To publish
reviewed prices/features and a saved research run, add entries such as:

```json
{
  "indices": {
    "sample": {"version_id": 1, "label": "Synthetic example"}
  },
  "reviews": {"sample-review": "review.json"},
  "benchmarks": {"sp500": "benchmark-review.json"},
  "runs": {"experiment-001": "experiment-001/report.json"}
}
```

Supply a valid [feature review](features.md#review-manifest), a separately reviewed
[benchmark](#financial-context), and an existing nine-feature report produced by
the [research command](research.md). These are format examples, not runnable
reviews or fitted experiments. Reports and reviews are read and validated
at application creation; restart to publish changed files. Each file is limited
to 64 MiB. The `indices`, `reviews` and `runs` catalogues allow up to 100 entries
each; `benchmarks` allows up to 10. A review supports up to 1,000 declared
identities. Callers send discovered identifiers, never filesystem paths.

## Endpoints

The table lists all 15 IndexLens endpoints. Linked descriptions lead to their request examples below. Swagger UI and the OpenAPI schema are supporting documentation URLs outside this table.

| Endpoint | Purpose and example |
|---|---|
| `GET /api/v1/health` | [Check database access and required tables](#health) |
| `GET /api/v1/indices` | [Discover published indices, source versions, coverage and data kinds](#identifier-discovery) |
| `GET /api/v1/indices/{index_id}/securities` | [Discover security IDs, exact source symbols and CIK strings](#identifier-discovery) |
| `GET /api/v1/reviews` | [Discover reviews, datasets, price basis, bounds and universe sizes](#identifier-discovery) |
| `GET /api/v1/research/runs` | [Discover saved runs, data kinds, report hashes and evaluation status](#identifier-discovery) |
| `GET /api/v1/securities/{security_id}` | [Retrieve original source records and separate membership intervals](#original-security-records) |
| `GET /api/v1/indices/{index_id}/membership` | [Retrieve membership on a date](#membership-on-a-date) |
| `GET /api/v1/indices/{index_id}/events` | [Retrieve ADD and REMOVE events in a date range](#events-in-a-date-range) |
| `GET /api/v1/securities/{security_id}/prices` | [Retrieve observed prices using a selected review](#prices-in-a-date-range) |
| `GET /api/v1/securities/{security_id}/features` | [Calculate existing features on a reference date](#features-on-a-reference-date) |
| `GET /api/v1/securities/{security_id}/financial-context` | [Dated descriptive financial indicators and benchmark evidence](#financial-context) |
| `GET /api/v1/rankings` | [Rank a review's universe by past returns](#momentum-rankings) |
| `GET /api/v1/research/runs/{run_id}` | [Retrieve three-way model metrics and the selected cutoff](#saved-research-summary) |
| `GET /api/v1/research/runs/{run_id}/records` | [Retrieve a page of saved research records](#saved-research-records) |
| `POST /api/v1/research/explain` | [Explain saved nine-feature research, company financial context, events or rankings](explanations.md) |

Braced names such as `{index_id}` are **path parameters**: replace them with discovered identifiers. Values after `?`, separated by `&`, are **query parameters**, such as `as_of=2026-01-07` or `limit=100`. Changing these values supplies different inputs to the same endpoint.

Each `curl` command below sends a GET request and prints the API's JSON response. The identifiers and dates in the examples are illustrative synthetic values. Discover published IDs from the API and substitute dates supported by their sources.

## Health

Check that the API responds and can read the required membership and price tables:

```sh
curl -i 'http://127.0.0.1:8000/api/v1/health'
```

The `-i` option also prints the HTTP status and response headers. Success returns `200` with `{"status":"ok"}`. A database access failure returns `503` with `error.code: database_unavailable`. A connection refusal means that no HTTP connection was established; check the server process, address and port. Starting the PostgreSQL container alone does not start the API server.

## Identifier discovery

See the [complete table of 15 endpoints](#endpoints) for paths and purposes.

A security identifier has the form `v<source_version_id>-s<identity_position>`, for
example `v1-s0`. It identifies a stored `(symbol, cik)` pair **within one source
version**, not a permanent security across files or installations. Discover IDs
from the API instead of constructing them from a ticker. Numeric ID components
use canonical decimal notation: `v1-s00` is invalid. Leading zeros in CIKs
remain significant text. Different symbols sharing a CIK and different CIKs sharing
a symbol remain separate. Multiple intervals for one identity retain separate
zero-based `source_record_position` values and original record hashes.

For portable references, retain the membership hash, exact source identity and
record positions. Saved research rows resolve to local security IDs using the
membership hash and real/synthetic kind. A saved report's database ID is not reused
blindly. If no unique published source matches, the returned `security_id` is null;
the saved symbol, CIK and membership hash remain available.

A review specifies which provider history can support an identity on particular
dates. An approved historical alias can share a provider history while retaining
its own internal ID, source positions and date bounds. A matching ticker alone
never authorizes a join.

List the published indices, then use a returned `index_id` to discover its securities
(`sample` is an illustrative index ID):

```sh
curl 'http://127.0.0.1:8000/api/v1/indices'
curl 'http://127.0.0.1:8000/api/v1/indices/sample/securities?limit=100'
```

List review IDs for price, feature and ranking requests:

```sh
curl 'http://127.0.0.1:8000/api/v1/reviews'
```

List run IDs for saved research requests:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/runs'
```

Each run ID selects one saved report. The listing provides IDs, report hashes and each saved `threshold`, so a cutoff can be selected before opening a summary. The same value appears in summary metadata and `result.evaluation.threshold`. Publish separate cutoff comparisons under separate configured run IDs. There is no threshold query parameter. Restart the API after changing the configuration or report files; removed IDs then return `404`.

For reports created by [saved cutoff evaluation](research.md#evaluate-cutoffs-from-a-saved-report), add entries to the API configuration's `runs` object. If `api.json` is in `data/research/`, the synthetic usage example above can be registered as:

```json
{
  "experiment-001": "experiment-001/report.json",
  "experiment-001-cutoff-0p002": "experiment-001-cutoffs/cutoff_0p002.json"
}
```

Merge these entries into `runs`, retaining other published IDs. Paths resolve relative to the API configuration file. Restart the process with that configuration, rediscover the IDs, and select `experiment-001-cutoff-0p002` for its summary or explanation. A running process retains its loaded snapshot until restart; unknown or removed IDs return `404`. Registration does not change report contents. Discovery and summaries reuse saved results and make no model fit or OpenAI request.

## Data queries

See the [complete table of 15 endpoints](#endpoints) for paths and purposes.

These eight endpoints retrieve or calculate results using discovered identifiers. Each example is followed by the rules governing its response. The identifiers and dates remain illustrative; use values supported by the selected sources.

### Original security records

Retrieve the original source rows and separate membership intervals for a discovered
security ID:

```sh
curl 'http://127.0.0.1:8000/api/v1/securities/v1-s0'
```

The response includes original field strings, record hashes, source-record positions,
interval dates and approximation flags. Multiple intervals for one identity remain
separate records.

### Membership on a date

List the member source rows for the selected index on 7 January 2026:

```sh
curl 'http://127.0.0.1:8000/api/v1/indices/sample/membership?as_of=2026-01-07'
```

Membership applies addition-inclusive, removal-exclusive and creation-date guards.
The requested date must lie within inclusive membership coverage. Source findings,
approximate dates and snapshot warnings remain visible.

### Events in a date range

List recorded ADD and REMOVE events from 1 through 31 January 2026:

```sh
curl 'http://127.0.0.1:8000/api/v1/indices/sample/events?start=2026-01-01&end=2026-01-31'
```

Events select the inclusive requested range from the existing source event window
`(coverage_start, coverage_end]`. The baseline coverage date does not acquire a new
event. Approximate dates, creation-after-event flags and source findings remain
visible. Events do not replace the guarded membership query.

### Prices in a date range

Retrieve observed prices for one security using the selected review's provider,
dataset and price basis:

```sh
curl 'http://127.0.0.1:8000/api/v1/securities/v1-s0/prices?review_id=sample-review&start=2026-05-01&end=2026-05-29'
```

Price queries select an inclusive observed-date range within the chosen provider,
dataset and basis. Membership coverage describes the membership source, not price
availability. Price queries can extend beyond membership coverage. Each price row
contains exact normalized OHLCV strings or nulls, numeric usability flags, conflict
status and source provenance. `attributed_to_security: false` identifies candidate
evidence blocked by mapping, date, basis, source or conflict checks; `unavailable`
explains those blocks. Numeric issues remain in provenance and usability flags.
The stored first candidate is retained for conflicted observations and is not a
preferred value. Empty price ranges return `empty_reason: no_observations_in_range`.

### Features on a reference date

Calculate the existing return, volatility and average-volume features for one
security on the requested date:

```sh
curl 'http://127.0.0.1:8000/api/v1/securities/v1-s0/features?review_id=sample-review&reference_date=2026-05-29'
```

Feature queries use the latest 21 chronological observation positions at or before
the requested date. The reference date must itself have an observation for features
to be available. Values are decimal strings, with nulls and reasons for incomplete,
unreviewed or invalid windows. Source decisions apply to every contributing date;
an unavailable window never borrows an older observation. Dates are exact and are
not automatically shifted to trading sessions.

### Momentum rankings

Rank the selected review's universe on the requested date. This example requests
same-date membership context and the first ten rows:

```sh
curl 'http://127.0.0.1:8000/api/v1/rankings?review_id=sample-review&reference_date=2026-05-29&membership=true&limit=10'
```

Rankings use the complete review universe and the existing common-date rule, then
paginate the results. Page size never changes the ranking universe. Unavailable
identities remain in the output with null ranks and reasons. `membership=true`
requires that same date to be both common and within membership coverage.
**Momentum ranks describe past returns; they are not removal probabilities.**

### Saved research summary

Retrieve the summary for a run ID returned by `/research/runs`:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/runs/experiment-001'
```

Research summaries retain class/event counts, split boundaries, parameters, scaling,
metrics, undefined-value reasons, computational unmet requirements and limitations.
The saved report hash identifies the exact loaded bytes. Private configuration paths
and full review evidence files are not included in report summaries. Evaluation
completion does not establish predictive validity. Synthetic results remain
explicitly synthetic.

The saved threshold and metrics are authoritative for that run. Changing the research command's default cutoff does not rewrite or reinterpret existing reports. Derived cutoff evaluations retain `result.evaluation.derivation`, including the source run ID, source report hash and exploratory flags. Reusing saved probabilities at another cutoff does not refit the model or establish performance on unseen data.

### Saved research records

Retrieve a page of saved predictions from the same run:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/runs/experiment-001/records?section=predictions&limit=100'
```

The `section` parameter selects which saved collection to retrieve. Predictions,
identity outcomes, excluded positive observations, snapshot issues, source hashes/counts and
excluded reference dates are paginated through `/research/runs/{run_id}/records`.
The `section` values are `predictions`, `identities`, `excluded_positives`,
`snapshot_issues`, `sources` and `excluded_reference_dates`. Complete candidate-level
observation artifacts remain part of the saved offline run; this API exposes the
report results.

Nine-feature prediction records include the observed outcome `label`, baseline,
matched-comparison and primary model probabilities, and the primary predictors,
observation date and input fingerprint. The outcome label is not a model
classification. Positive classifications use `probability >= result.evaluation.threshold`
from the selected report. Derived cutoff reports reuse the same probabilities;
cutoff-dependent metrics can differ.

## Limits and errors

Collection responses contain `items`, `total`, `offset`, `limit`, `next_offset` and
`metadata`. Follow `next_offset` until null, keeping other parameters fixed. The
default limit is 100, the maximum is 200, and offsets are between 0 and 1,000,000.
Event and price ranges are limited to 366 inclusive calendar days. Payloads are
limited to 2 MiB; a larger payload receives 422 with instructions to reduce the
page size or range. Dates must use `YYYY-MM-DD`. Database reads use their own read-only,
repeatable database transaction with a 15-second statement timeout. Separate pages
are separate transactions; keep data loading separate when a stable multipage
snapshot is required. Saved run listings and summaries use the startup snapshot and
remain available during a database outage. Run records use the database to resolve
local security IDs.

| Status | Meaning |
|---|---|
| `200` | Valid result, including empty collections or unavailable values with reasons |
| `404` | Unknown index, security, review or run identifier |
| `422` | Invalid syntax, reversed/oversized range, unsupported membership date, incompatible identity/review or response bounds |
| `503` | Database or configured evidence unavailable/inconsistent |

Error bodies contain `error.code` and `error.message`. Parameter validation errors
also identify the affected fields. Database credentials and SQL details are omitted.

Routes validate HTTP inputs, services apply query/identity contracts, and repositories
read existing database evidence or configured saved files. The feature endpoint uses
the bounded 20-session calculations; financial context reads up to 121 observations. Synchronous data handlers run in
FastAPI's worker threads. Research training and ingestion remain separate commands. The
[explanation endpoint](explanations.md) calls OpenAI after retrieving its evidence;
GET endpoints remain usable when OpenAI is unavailable. See [FastAPI concurrency](https://fastapi.tiangolo.com/async/).
The API binds locally in the startup example; hosting and access control require
separate deployment configuration.

## Financial context

`GET /api/v1/securities/{security_id}/financial-context?review_id=sample&reference_date=2026-07-31&benchmark_id=sp500` returns descriptive indicators and their source evidence. `benchmark_id` is optional; omitted or unavailable benchmark history produces null market comparisons. An unknown configured benchmark ID returns 404; mixing real and synthetic data returns 422. The route computes at most 121 observations per series and makes no external requests.

Configure `benchmarks` in the existing API configuration as a map from identifiers to review JSON paths, for example `"benchmarks": {"sp500": "benchmark-review.json"}`. Restart to load changes. A benchmark review contains:

- `review_version: "benchmark-review-v1"`, `review_id`, `reviewed_on`, `dataset_id`, `data_kind`, `provider`, `price_basis`, `adjustment_settings`, `limitations`, `currency: "USD"`, and `return_convention: "price_return"`.
- `basis_review` with the same decision fields as the [feature review](features.md#review-manifest).
- `benchmark` with decision fields `status`, `start`, `end`, `description`, `evidence`, plus `provider_symbol` (`^GSPC` or `SPY`), `instrument_type` (`INDEX` or `ETF` respectively), and `sources` containing exact `source_sha256` and `metadata_sha256` pins.

The benchmark is reviewed independently of membership. Stock mappings still use source-scoped security IDs and the existing reviewed-history gates. The optional stock-review policy and formulas are documented in [financial context](features.md#descriptive-financial-context). Existing feature, ranking and saved-run endpoints retain their contracts.

## Nine-feature reports

Register the primary report and its five derived cutoff files under distinct
`runs` IDs. Discovery returns the selected report’s threshold, version and hash.
`monthly-removal-nine-v1` summaries contain the constant baseline, matched comparison
and primary nine-feature evaluation, with common cohort counts, independent scalers,
fitted parameters and metrics. Predictions retain aligned probabilities, exact dated
primary model inputs and fingerprints.

A financial-context explanation can attach a supported nine-feature report when the source-scoped security, membership hash, data kind, stock and benchmark reviews, exact reference/observation date, input hash and predictor values agree. A mismatch returns `422 research_context_mismatch`; an absent or ambiguous prediction returns `422 prediction_unavailable`. No weekend-to-session adjustment is performed by this join. The selected report controls the cutoff. Supplementary missingness does not prevent an otherwise matching prediction.
