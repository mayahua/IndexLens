# Numerical features and momentum ranking

In this guide, a **feature** is a calculated numerical measure derived from
historical market data; a **model predictor** is a feature selected as an input
to a predictive model. IndexLens calculates and uses these features in three ways:

- **Momentum ranking (`observed-20-v2`):** Three short-window features—20-session
  return, volatility and average share volume—support descriptive rankings of a
  declared security universe by return.
- **Removal research (`removal-nine-features-v1`):** Nine predictors based on
  price, activity and benchmark-relative histories support [monthly S&P 500 removal research](research.md).
- **Financial context (`financial-context-v1`):** Sixteen features, including
  those nine predictors, provide broader historical context for analysis and
  [evidence-backed explanations](explanations.md).

Momentum rankings describe past performance, the removal model estimates a
defined future outcome, and financial context provides supporting evidence.
The original feature endpoint remains available separately from the
financial-context endpoint.

Load prices using [the price guide](prices.md) and configure PostgreSQL using
[the database guide](database.md).

## Query without a notebook

Supply a review manifest (a JSON file recording review decisions) and an observation
date. The dates and identifiers in this guide are synthetic examples; substitute
values supported by your data:

```sh
python -m indexlens.features --review path/to/review.json --date 2026-05-29
```

This returns the return, volatility and volume features, plus a descending
**momentum ranking** for the manifest's full declared universe. An observation on the requested date must
exist for every identity before any ranks are assigned. The command never picks
a different date per security or searches backwards to hide an unavailable
window. Choose a common date using the ingestion coverage reports; existence of
a date alone does not approve its inputs.

To include membership information for that same date:

```sh
python -m indexlens.features --review path/to/review.json \
  --date 2026-05-29 --membership
```

The membership source must match the review's file hash and real/synthetic kind.
Its reviewed coverage must include the observation date. Context uses the existing
addition, removal and creation date rules and preserves source warnings. An unsupported
membership date fails; an older snapshot is never attached as current context.
Without `--membership`, no membership context is attached.

Exit status is 0 when every declared identity has a rank, 2 when the report
contains unavailable ranks, and 1 for invalid setup/review or unsupported requested
membership context. Missing volume can leave a return rank available while volume
is null: inspect each feature's `unavailable` field as well as `ranking_complete`.
The command uses a consistent, read-only view of PostgreSQL data and does not
fetch data, edit stored evidence, or replace failures with synthetic observations.

## Formulas and unavailable values

| Output | Window ending on the observation date | Calculation |
|---|---|---|
| `return_20` | 21 valid closes | last close / first close − 1 |
| `volatility_20` | 21 valid closes producing 20 daily simple returns | sample standard deviation, divisor 19 (`ddof=1`), unannualized |
| `average_volume_20` | 20 valid volumes | sum / 20 |

Windows use chronological **observed sessions**, not calendar days. Each dated observation
keeps its position, including incomplete, invalid or conflicting rows. Missing values
are never filled with zero or copied from earlier rows. Incomplete rows are never
removed to pull an older value into the window. An entirely absent session is not
invented; gaps remain in ingestion coverage reports. This is an observed-session calculation, not a guarantee of
complete exchange-calendar history.

Every input date must fall inside both mapping and basis approval, and every
source contributing to the observation must be identified by hash in the review. Too few
observations, missing values, invalid prices, conflicts, unreviewed sources and unapproved dates
produce JSON nulls with reasons. Volume zero is valid. Missing or invalid volume
blocks average volume without blocking valid closing-price calculations. Missing
opening, high or low prices do not block return or volatility when all required
closing prices are valid. Invalid price values or impossible price ranges still
block price features and volume under the shared ingestion rules. A missing close
blocks return and volatility, while valid volume can remain available. An
unapproved oldest price date can block 21-close features while leaving the
20-volume window available.

Version `observed-20-v2` corrects the earlier requirement for complete opening,
high, low and closing prices when calculating closing-price features. Its input
records report `close_usable` in place of `prices_usable`. This describes whether
the close can be used; stored ingestion flags remain unchanged. Approval and
complete-window checks are still required. The formulas are unchanged.

Calculations parse exact normalized decimal strings and use 34-digit decimal
arithmetic. JSON feature values are decimal **strings** (returns in fractional
units, not percent); unavailable values are null. Sorting uses computed returns
before display rounding, then exact `(source symbol, source CIK)` text. Ties receive
successive ranks in that order. CIK alone never merges share classes.

## Review manifest

The review records supplied decisions; it does not independently prove identity.
The original unresolved flags from loading remain unchanged. The feature code
applies a separate review identified by its hash, without changing those flags or
renaming the stored basis. Changing source bytes, retrieval metadata, settings,
basis, dataset, dates or identity requires a new review. Approval does not
transfer automatically between providers, datasets or date ranges.

The example below uses a fictional security, synthetic data and placeholder
hashes. It illustrates the format and is **not an approved or runnable manifest**.
Declare every intended security in `universe`, including unresolved ones. Source
identities must be unique within the manifest; ambiguous mappings are rejected.
All review date bounds are inclusive.

Historical ticker aliases can explicitly share one approved provider history when
the CIK, source hashes and bounded security review agree. See the
[research workflow](research.md) for `historical_alias_of` and retained request
outcomes. Undeclared sharing of a provider ticker remains invalid.

```json
{
  "review_version": "feature-review-v1",
  "review_id": "synthetic-example-review-v1",
  "reviewed_on": "2026-06-01",
  "dataset_id": "synthetic-example-v1",
  "data_kind": "synthetic",
  "provider": "synthetic_fixture",
  "price_basis": "synthetic_close",
  "adjustment_settings": {},
  "membership_sha256": "<64-character membership file SHA-256>",
  "limitations": "Synthetic example only; no real security mapping.",
  "basis_review": {
    "status": "unresolved",
    "start": "2026-04-01",
    "end": "2026-05-29",
    "description": "Specify the source field and price basis being reviewed.",
    "evidence": []
  },
  "universe": [
    {
      "symbol": "EXAMPLE_A",
      "cik": "0000000001",
      "provider_symbol": "EXAMPLE_A",
      "source_record_positions": [
        0
      ],
      "status": "unresolved",
      "start": "2026-04-01",
      "end": "2026-05-29",
      "evidence": [],
      "sources": []
    }
  ]
}
```

To prepare a review, inspect issuer/security evidence and provider metadata, record
source `(symbol, cik)` pairs and record positions counted from zero, and identify
the exact price sources by their hashes. The existing tables provide the metadata:

```sql
SELECT id, source_sha256, metadata_sha256, declarations
FROM price_sources WHERE dataset_id = 'synthetic-example-v1';
SELECT id, dataset_sha256, dataset_kind, coverage_start, coverage_end
FROM source_versions;
SELECT source_record_position, original_fields
FROM source_rows WHERE version_id = <reviewed_version_id>;
```

An approved decision needs nonempty evidence references. Each approved mapping's
`sources` list contains objects with `source_sha256` and `metadata_sha256` from
`price_sources`. These identify the exact CSV contents and complete metadata;
they are distinct from any raw chart response hash inside `declarations`.
Use `approved` only after reviewing the stated dates and inputs. `unresolved` entries
remain in results with null features. A matching ticker or successful fetch alone
is insufficient. The manifest reader checks the format; it cannot establish the truth
of a human review. Real and synthetic reviews must use separate matching datasets
and membership sources.

## Price basis and provenance

Features use the stored normalized Close and volume for the explicitly selected
basis. The query does not switch to a different close field or apply additional
corporate-action adjustments. State the source field and adjustment settings in
the review; settings must match the stored source declarations.

Close-based price changes do not include dividend reinvestment. Corporate actions
and the provider's adjustments can affect interpretation. Approval for a stated
basis does not establish that the values are the prices originally traded or
returns including reinvested dividends.

Each report includes dataset ID, real/synthetic kind, provider, stored basis and
reviewed basis description, adjustment settings, feature version, observation
date, declared universe, review contents/hash, exact window dates, input values,
row/source hashes and positions, original retrieval times and raw-response
references. PostgreSQL IDs help local inspection; hashes support comparison
across installations. Save the JSON with its review so the results can be reproduced.

Tests cover the numerical formulas, review checks and command behavior using
synthetic fixtures and isolated PostgreSQL schemas.

## Feature roles

The feature endpoint and momentum rankings use the short-window calculations above. They describe past price changes and trading activity. The [removal research model](research.md#fixed-comparison-and-metrics) uses nine explicitly versioned predictors calculated from reviewed stock and benchmark histories. Additional descriptive features support company-level context and explanations but are not automatically model inputs.

## Descriptive financial context

The financial-context API uses version `financial-context-v1`. Its sixteen features include all nine removal-model predictors plus seven supplementary features for analyst-style descriptions. The separate `observed-20-v2` feature endpoint remains available for momentum rankings.

| Feature | Complete window | Calculation / unit |
|---|---|---|
| `return_20`, `return_60`, `return_120` | 21, 61, 121 closes | last / first − 1, fraction |
| `volatility_20` | 21 closes | sample standard deviation of 20 simple returns, divisor 19, daily and unannualized |
| `average_dollar_volume_20`, `average_dollar_volume_60` | 20 or 60 paired closes and volumes | mean(Close × volume), approximate USD per observed session |
| `drawdown_120` | 120 closes including the reference date | current / highest close − 1 |
| `benchmark_return_20/60/120`, `excess_return_20/60/120` | identical stock/benchmark date sequences | benchmark price return; stock return minus benchmark return |
| `benchmark_volatility_20`, `relative_volatility_20` | identical 21-date sequences | benchmark sample standard deviation; stock volatility / benchmark volatility |
| `dollar_volume_20_to_60_ratio` | complete component windows | shorter mean / longer mean |

Returns and differences are fractional values, not percentages. Volatility ratios above unity indicate greater historical variability, not a forecast. A zero denominator produces null. Drawdown is distance below a trailing closing high, not the maximum peak-to-trough loss. The dollar-volume windows overlap, so their ratio compares recent activity with a broader baseline rather than measuring growth between consecutive periods. Dollar volume is not transaction-level turnover or the official S&P liquidity eligibility test.

The reader selects at most 121 dated observations per series before validity checks. It ignores future dates and requires an observation on the requested date. Missing, invalid and unapproved rows retain their positions. Benchmark windows must have exactly the stock window's dates, including intermediate dates; an intersection or forward-fill cannot repair gaps. Entirely absent dates are not invented, and these remain observed-session calculations rather than guaranteed complete exchange-calendar windows.

Stock reviews may add `financial_context` with `currency: "USD"`, `return_convention: "price_return"` and an optional `dollar_volume_review`. The latter uses the same status, inclusive start/end, description and evidence fields as `basis_review`. Approve it only for paired Close and volume with a reviewed compatible adjustment basis. Each dollar-volume window must fall inside its approval dates. Missing approval leaves dollar volume null; other valid features remain available. Yahoo context requires the existing quote Close basis and unchanged adjustment settings; Adj Close is never substituted. Split-adjusted prices must be paired with consistently adjusted volume. A review records a decision, not independent proof of provider correctness.

Financial-context results include exact dates, units, missingness, public input provenance, an input hash, review hashes and selected benchmark identity. Calculations are retrospective on pinned provider-returned data. Excluding future observation dates does not certify that later-retrieved, possibly revised prices were available in exactly that form historically.

## Fitted predictors and descriptive inventory

The primary `removal-nine-features-v1` order is recorded in the [research guide](research.md#fixed-comparison-and-metrics). It reuses the existing calculations and complete-window rules. Model eligibility requires those nine predictors and the historical source, membership and label checks. Supplementary 120-session values can remain null.

The financial-context GET response contains sixteen features, including the nine model predictors. The separate feature endpoint exposes `average_volume_20` (average share volume), which differs from average **dollar** volume and is not a fitted input of the nine-feature model. Supplementary features remain independently available or null with reasons, rather than affecting research eligibility.

Financial evidence labels saved `fitted_features` and `fitted_values` separately from `supplementary_indicators`. The calculation's original input fingerprint is exposed as `research_input_sha256` for exact saved-input matching; `input_sha256` continues to fingerprint the public GET inputs.
