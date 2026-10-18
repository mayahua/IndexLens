# Monthly removal research

The research command asks whether an active member has a recorded removal in the
next 30 calendar days. It compares a constant probability with logistic regression
using the three [numerical features](features.md). Removal probabilities answer a
different question from descriptive momentum ranking.

## Run a saved experiment

Install `.[dev]` and configure PostgreSQL using the [database guide](database.md).
Load membership and prices using the existing loaders. Prepare a complete population
manifest, a dated [feature review](features.md#review-manifest), and the corresponding
[ingestion report](prices.md#manifest-ingestion). Run from the repository root:

```sh
python -m indexlens.research --config path/to/run-config.json \
  --output data/research/experiment-001
```

The configuration format is:

```json
{
  "config_version": "removal-run-v1",
  "seed": 42,
  "manifest": "path/to/population.json",
  "review": "path/to/review.json",
  "ingestion_report": "path/to/ingestion-summary.json",
  "evidence_files": [
    {"path": "path/to/review-evidence.json", "sha256": "<SHA-256 of file bytes>"}
  ]
}
```

Paths resolve from the working directory. Evidence files are optional; when listed,
their hashes must match. The ingestion report must pin the canonical population manifest
content and list every declared identity. Reviews must retain unresolved identities.
Keep the configuration, source cache, review evidence and database inputs together
when reproducing a run. The output directory must be new and under `data/`.

The population manifest extends the ingestion manifest with these fields. Dates
below are **synthetic examples**, not historical research approval:

```json
{
  "version": "synthetic-population-v1",
  "data_kind": "synthetic",
  "source_sha256": "<membership CSV SHA-256>",
  "coverage_start": "2025-01-01",
  "coverage_end": "2026-09-30",
  "train_reference_start": "2025-01-31",
  "train_outcome_cutoff": "2025-12-31",
  "test_reference_start": "2026-01-31",
  "test_reference_end": "2026-07-31",
  "label_horizon_days": 30,
  "price_request_envelope": {
    "start_inclusive": "2025-01-01",
    "end_exclusive": "2026-08-01"
  },
  "eligible_source_identity_count": 1,
  "identities": [
    {"symbol": "EXAMPLE_A", "cik": "0001", "source_record_positions": [0]}
  ]
}
```

These placeholder files illustrate the format. A runnable manifest must match the
loaded source, its coverage, every identity with a source-eligible observation, and
all relevant source record positions. The command recalculates the population and
rejects a shortened list. Real and synthetic sources, reviews and prices must agree.
It makes no provider requests and never substitutes synthetic records for failures.

An explicitly reviewed historical ticker can reuse a loaded history from its later
name. Set `provider_symbol` to the stored provider ticker and `historical_alias_of`
to the owner entry's source symbol. Both entries must be approved, have the same
CIK and source hashes, and the alias dates must lie within the owner's approved
range. The review must supply evidence of the same security class through the
change. Implicit sharing, different issuers and alias chains are rejected. The
research report retains the original failed request and identifies the loaded
history used for each observation. A failed history owner still excludes the row.


Exit status is **0** when both models are evaluated on a test set containing both
classes, **2** when a retained report has insufficient observations/classes or a
model fails to converge, and **1** for invalid setup. Status 0 describes a completed
calculation; it does not establish representative coverage or predictive validity.

## Labels, dates and exclusions

At each calendar month-end, the database membership query applies the existing
addition, removal and creation-date guards. A positive label requires a REMOVE
in `(reference_date, reference_date + 30 calendar days]` whose source record
position belongs to that active interval. A matching ticker in a different interval
cannot supply the label. A negative means no matching recorded event in a complete
outcome window, rather than proof that no real-world removal occurred.

Incomplete outcomes, duplicate active identities, multiple active CIKs for a symbol,
approximate interval boundaries and removal-date conflicts leave the label null.
The approximation screen applies to the whole active interval. Nominal labels and
exclusion reasons are retained for audit; they never become model inputs. Training
references whose outcomes extend beyond the training cutoff are recorded separately
as excluded dates. Training outcomes must finish before testing begins.

Feature dates use the last scheduled **XNYS** session on or before the reference.
For example, Sunday 31 May 2026 uses Friday 29 May. If Friday's observation is absent,
Thursday cannot replace it. Each window uses `observed-20-v2`: 21 closes for return
and sample volatility, and 20 volumes for average volume. Invalid, incomplete and
conflicting dated records retain their positions. Entirely absent interior sessions
remain an ingestion coverage limitation; these are observed-session features.

Every contributing date must pass mapping and basis approval, and every contributing
source must match the review's hashes and settings. The active interval must also
be covered by the review's source positions. Only dates within the declared price
envelope and at or before the feature date can contribute. A row becomes eligible
only when its label and all three features are available. Source-only and final
counts are both reported, including distinct removal events.

## Fixed comparison and metrics

The baseline probability is the positive fraction of **eligible training rows**.
Both models use exactly the same eligible test observations. The classifier uses
`StandardScaler` fitted only on training rows, followed by logistic regression with
L2 regularization, `C=1`, `solver=lbfgs`, an intercept, no class weights, `tol=1e-8`
and at most 1,000 iterations. The configured seed is recorded; this solver's fit is
deterministic for fixed inputs. There is no tuning or model selection on test data.
The classification rule is probability **greater than or equal to 0.5**.

The model input order is `return_20`, `volatility_20`, `average_volume_20`.
No identity, future outcome, event date, retrieval date or review information enters
the numerical predictor matrix. Scaling means, variances and scales, model
coefficients, intercept and test probabilities are saved.

Precision, recall, F1, average precision, confusion matrix and Brier score are
reported. Zero denominators produce nulls with reasons. With positives but no
predicted positives, precision is undefined, recall and F1 are zero. With no positive
test outcomes, recall and average precision are undefined. Brier score is the mean
squared probability error. Confusion-matrix rows are actual `[0, 1]` and columns are
predicted `[0, 1]`. A single training class prevents a logistic fit; the baseline and
eligibility report remain available. A single test class is explicitly inconclusive.

The [scikit-learn model](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.LogisticRegression.html),
[scaling](https://scikit-learn.org/1.7/modules/generated/sklearn.preprocessing.StandardScaler.html)
and [average precision](https://scikit-learn.org/1.7/modules/generated/sklearn.metrics.average_precision_score.html)
documentation describe the library operations used here.

## Saved evidence and interpretation

`report.json` retains configuration, dataset/kind, versions, code revision and
working-tree status, code/input/review hashes, package versions, reference and
outcome boundaries, identity outcomes, source metadata, exclusions, class/event
counts, parameters and metrics. Exact input JSON bytes are copied alongside it.
`observations.jsonl.gz` contains one JSON record per candidate, including null labels,
features, reference and actual window dates, exclusion reasons, input hashes and
source row references. The source catalogue connects local source IDs to immutable
CSV/metadata hashes and original retrieval evidence. The compressed observation
file has a recorded hash and deterministic contents for unchanged inputs.

A read-only repeatable database transaction keeps each run internally consistent.
Existing tables and migrations are reused. No stored membership, events, price
observations or original approval flags are changed.

Historical downloads and retrospective membership records do not prove what was
available at an earlier date. Provider revisions and corporate-action adjustments
can change historical values. Returned Close is distinct from Adj Close, original
as-traded prices and total returns. A review must state the accepted basis and its
limits. Historical security continuity requires dated evidence and explicit
assumptions; a matching ticker alone is insufficient.

Missing acquired or delisted histories and uneven mapping evidence can select an
unrepresentative sample. Repeated monthly observations of one removal are counted
as multiple observations but one event. Securities and removals clustered on the
same dates are also dependent. Small positive-event counts make apparent performance
unstable. Preserve these limitations and any inconclusive results when presenting
the experiment.

## Limitations and further work

The model uses a small number of features with straightforward formulas to assess whether recent trading patterns help predict removals. The 20-session return measures price change, volatility measures variation in daily returns, and average volume measures trading activity. The current experiment evaluated them together against the constant-probability baseline; **reliable removal prediction has not been established**.

Whether other features or different combinations of features would improve model performance has not been tested. The 0.5 classification cutoff is also fixed in advance; it has not been
selected to balance missed removals against false alarms. It can produce no positive
predictions when estimated removal probabilities are low.

Two possible extensions are:

- **Feature selection:** consider new features such as longer-term returns,
  company size or financial performance, and compare feature combinations using
  chronological validation within the training period. Fit preprocessing and
  candidate models on earlier training rows, then use later validation rows to
  choose the inputs.
- **Cutoff selection:** define an evaluation objective, such as catching more removals
  while limiting false alarms, and compare probability cutoffs on validation
  predictions. Record the resulting precision/recall tradeoff and fix the chosen
  cutoff before final testing.

However, few positive events can make either selection process unstable, and neither extension guarantees better performance.
