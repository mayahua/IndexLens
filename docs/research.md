# Monthly removal research

The research command asks whether an active member has a recorded removal in the
next 30 calendar days. The primary exploratory specification uses nine price and
benchmark-relative predictors. It compares a constant training-prevalence baseline,
the original three-feature logistic regression and the nine-feature logistic
regression on exactly the same eligible cohort. Removal probabilities answer a
different question from descriptive momentum ranking.

Feature selection followed repeated inspection of the historical test period; these results do not establish independent predictive improvement.

## Fit and save an experiment

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
  "config_version": "removal-nine-run-v1",
  "seed": 42,
  "cutoff": 0.05,
  "manifest": "path/to/population.json",
  "review": "path/to/review.json",
  "ingestion_report": "path/to/ingestion-summary.json",
  "benchmark_review": "path/to/benchmark-review.json",
  "evidence_files": [
    {"path": "path/to/review-evidence.json", "sha256": "<SHA-256 of file bytes>"}
  ]
}
```

The optional `cutoff` is a finite JSON number in `[0, 1]`; booleans, strings and nonfinite values are rejected. If omitted, the default value for new fits is **0.05**. `--cutoff 0.002` overrides a valid configuration value. Classification uses **probability >= cutoff**, including equality and endpoints. The cutoff changes decisions and metrics; fitted parameters and probabilities remain identical for the same training inputs. Historical saved thresholds, including 0.5, remain authoritative.

`removal-nine-run-v1` selects the nine-feature model with fixed seed 42. It requires a [benchmark review](api.md#financial-context) and a stock review with the USD price-return and dated dollar-volume policy described in [financial context](features.md#descriptive-financial-context). Ingest the benchmark over the full research price envelope, including training warm-up. No provider request occurs during fitting.

The saved versions are `monthly-removal-nine-v1`, `removal-nine-features-v1` and `scaled-logistic-nine-v1`. The original `removal-run-v1` and `removal-run-v2` configurations still select the legacy three-feature workflow and emit `removal-run-v2` effective configuration. `config.input.json` preserves the supplied bytes and hash. Historical reports, their versions and thresholds remain authoritative.

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


Exit status is **0** when all required models are evaluated on a test set containing both
classes, **2** when a retained report has insufficient observations/classes or a
model fails to converge, and **1** for invalid setup. Status 0 describes a completed
calculation; it does not establish representative coverage or predictive validity.

## Evaluate cutoffs from a saved report

Use a complete saved report and its referenced `observations.jsonl.gz`:

```sh
python -m indexlens.research evaluate-cutoffs \
  --source data/research/experiment-001/report.json \
  --source-run-id experiment-001 \
  --cutoffs 0.1 0.05 0.01 0.005 0.002 \
  --output data/research/experiment-001-cutoffs
```

`--source-run-id` records the source's registered or intended API ID. Add `--source-sha256 <expected SHA-256>` to pin a previously recorded report hash. The command checks that hash when supplied, verifies the observation artifact hash and row count, aligns prediction identities/dates/labels with eligible saved test rows, and checks saved metrics and summary counts. It rejects incomplete results, malformed labels/probabilities, duplicate cutoffs and existing output locations. The fresh output directory may be outside `data/`. Exit status is 0 for a completed export, including a labelled single-class evaluation, or 1 for invalid input or an output error.

The command reads local saved evidence and recalculates metrics through the same function used by fitting. It makes no database connection, download, feature/label rebuild or model fit. Each cutoff receives a separate file, such as `cutoff_0p002.json`; terminal output lists the files, thresholds and hashes. All reported models receive updated precision, recall, F1, confusion matrices and `predicted_removals` alert counts. Average precision and Brier score remain unchanged.

Derived reports retain the source's fitted parameters, prediction records, data kind, configuration and fitting provenance. Their effective decision cutoff is `evaluation.threshold`, repeated in each model’s metrics and `evaluation.derivation.selected_threshold`. The versioned `saved-cutoff-v1` derivation records the source ID, relative report reference/hash, source threshold/time, evaluation time, code hashes/revision and package versions. Prior derivation evidence is retained when evaluating a derived report. Source limitations are explicitly attributed and supplemented by the selected-cutoff limitation and exploratory warning. The source configuration continues to describe the fit, not the new evaluation.

Original artifacts remain unchanged. Observation evidence is shared through a relative path and its existing hash; preserve the relative directory layout when moving reports. Source report and observation hashes verify the local evidence used here; historical fitting-input hashes are retained as provenance, not revalidated against the database or original market files. Comparisons on inspected test data are exploratory and cannot establish an optimal cutoff or independent predictive validity.

Register the generated files under distinct run IDs and restart the API as described in [run discovery](api.md#identifier-discovery), then request an [explanation of the selected report](explanations.md#saved-research-run).

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
Thursday cannot replace it. The original features use `observed-20-v2`: 21 closes for return
and sample volatility, and 20 volumes for average volume. The nine-feature
specification also needs aligned benchmark history and up to 61 closes. Invalid, incomplete and
conflicting dated records retain their positions. Entirely absent interior sessions
remain an ingestion coverage limitation; these are observed-session features.

Every contributing date must pass mapping and basis approval, and every contributing
source must match the review's hashes and settings. The active interval must also
be covered by the review's source positions. Only dates within the declared price
envelope and at or before the feature date can contribute. A row becomes eligible
only when its label, applicable reviews and required model predictors are valid.
Missing 120-session returns or drawdown remain explicit supplementary gaps and do
not exclude a nine-feature observation. No unavailable value is imputed. Source-only and final
counts are both reported, including distinct removal events.

## Fixed comparison and metrics

The baseline assigns every test observation the positive fraction of **eligible training rows**. It uses no features and cannot rank observations by removal risk. Comparing its probabilities with logistic regression assesses whether learned predictors improve probability estimates and ranking over this constant reference. The matched three-feature model is refitted using exactly the nine-feature training and test cohort. Comparing a new model with an older report on a different cohort would mix model changes with coverage changes. The classifier uses
`StandardScaler` fitted only on training rows, followed by logistic regression with
L2 regularization, `C=1`, `solver=lbfgs`, an intercept, no class weights, `tol=1e-8`
and at most 1,000 iterations. The configured seed is recorded; this solver's fit is
deterministic for fixed inputs. The model parameters are fixed; the cutoff is an
exploratory choice. The classification rule is probability **greater than or equal
to the selected cutoff**, defaulting to 0.05 for new runs.

The versioned primary input order is:

1. `return_20`
2. `volatility_20`
3. `return_60`
4. `average_dollar_volume_20`
5. `average_dollar_volume_60`
6. `excess_return_20`
7. `excess_return_60`
8. `relative_volatility_20`
9. `dollar_volume_20_to_60_ratio`

The matched comparator uses `return_20`, `volatility_20`, `average_volume_20`.
`evaluation.logistic` is the primary model, `evaluation.matched_three` is the
refitted comparator, and `evaluation.baseline` is training prevalence. Each saved
prediction contains all three probabilities; the primary predictor values,
observation date, input hash and source-record positions are also retained.
No identity, future outcome, event date, retrieval date or review information enters
the numerical predictor matrix. Scaling means, variances and scales, model
coefficients, intercept and test probabilities are saved.

### Evaluation metric definitions

A positive outcome is a recorded removal. A **true positive (TP)** is a correctly predicted removal; a **false positive (FP)** is a predicted removal that did not occur. A **false negative (FN)** is a missed removal; a **true negative (TN)** is a correctly predicted non-removal.

| Metric | Definition | Range | Interpretation |
|---|---|---|---|
| **Precision ↑** | TP / (TP + FP) | [0, 1] | Proportion of predicted removals that actually occurred. |
| **Recall ↑** | TP / (TP + FN) | [0, 1] | Proportion of actual removals correctly identified. |
| **F1 score ↑** | 2 × precision × recall / (precision + recall) | [0, 1] | Harmonic mean summarizing the balance between precision and recall. |
| **Average precision ↑** | Weighted average of precision across recall increases | [0, 1] | How effectively actual removals rank ahead of non-removals across cutoffs. |
| **Brier score ↓** | mean((p − y)²), where p is the predicted probability and y is the actual outcome (0 or 1) | [0, 1] | Mean squared probability prediction error. |

**↑** Higher is better; **↓** lower is better. Ranges apply when a metric is defined. Precision, recall and F1 can also be expressed as percentages. The confusion matrix reports TP, FP, FN and TN as counts, with actual classes `[0, 1]` in rows and predicted classes `[0, 1]` in columns.

Average precision and Brier score evaluate probabilities independently of the chosen classification cutoff. Precision, recall, F1 and the confusion matrix evaluate removal alerts produced by applying that cutoff. Both sets of metrics are reported for each model. Zero denominators produce nulls with reasons. With positives but no predicted positives, precision is undefined, while recall and F1 are zero. With no positive test outcomes, recall and average precision are undefined.

With very few removals, assigning small probabilities to everyone can already produce a low Brier score; it is not, by itself, proof of useful discrimination or calibration. Lowering a cutoff typically increases recall and the false-alert burden. The fixed grid illustrates these operating trade-offs; no cutoff is described as optimal. A single training class prevents a logistic fit; the baseline and eligibility report remain available. A single test class is explicitly inconclusive.

The [scikit-learn model](https://scikit-learn.org/1.7/modules/generated/sklearn.linear_model.LogisticRegression.html),
[scaling](https://scikit-learn.org/1.7/modules/generated/sklearn.preprocessing.StandardScaler.html)
and [average precision](https://scikit-learn.org/1.7/modules/generated/sklearn.metrics.average_precision_score.html)
documentation describe the library operations used here.

## Saved evidence and interpretation

`report.json` retains configuration, dataset/kind, versions, code revision and
working-tree status, code/input/review hashes, package versions, reference and
outcome boundaries, identity outcomes, source metadata, exclusions, class/event
counts, parameters and metrics. Exact input JSON bytes are copied alongside it;
`config.input.json` retains the supplied configuration and `config.json` records its
effective settings.
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

## Experiment results

The supported nine-feature experiment uses dataset `yahoo-research-full-population-v2`, research version `monthly-removal-nine-v1`, feature version `removal-nine-features-v1` and model version `scaled-logistic-nine-v1`. Its benchmark uses the reviewed S&P 500 price-index series. Stock and benchmark inputs are pinned Yahoo-returned Close values with adjustment settings recorded in their reviews; retrospective source availability and price adjustments remain limitations.

Training references span January 2022–November 2024, with outcomes ending by 31 December 2024. Test references span January 2025–July 2026. All three model comparisons use the same eligible training and test observations.

| Eligible data | Training | Test |
|---|---:|---:|
| Observations | 15,289 | 9,252 |
| Positive observations / distinct removal events | 25 | 19 |
| Security identities | 503 | 513 |

The constant baseline uses the eligible training removal frequency, approximately 0.1635%, for every test row. The matched three-feature model is refitted on the same training cohort as the nine-feature model; neither is evaluated on a more favorable subset.

| Test measure | Constant baseline | Matched three-feature model | Nine-feature model |
|---|---:|---:|---:|
| Average precision | 0.00205361 | 0.02853667 | 0.03683280 |
| Brier score | 0.00204957 | 0.00203863 | 0.00202765 |

The nine-feature model has higher average precision and a slightly lower Brier score than both comparators on this retrospective sample. Average precision reflects removal ranking; the Brier score measures mean squared probability error. Rare positives allow even the constant baseline to achieve a low Brier score, so these results alone do not establish useful or calibrated predictions on new data. Comparing the logistic models on the same eligible observations avoids confusing model differences with differences in historical data coverage.

At the illustrative cutoff of **0.05**, the constant baseline raises no alerts. The matched three-feature model raises eight (one true positive and seven false positives). The nine-feature model raises seven (one true positive and six false positives), missing eighteen of the nineteen test removals. Its precision is 14.29%, recall 5.26% and F1 7.69%.

The fixed cutoff grid below uses **one nine-feature fit**. Changing the cutoff does not retrain the model or change its probabilities, average precision or Brier score.

| Cutoff | Alerts | True positives | False positives | Missed removals | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.1 | 3 | 0 | 3 | 19 | 0% | 0% | 0% |
| 0.05 | 7 | 1 | 6 | 18 | 14.29% | 5.26% | 7.69% |
| 0.01 | 152 | 6 | 146 | 13 | 3.95% | 31.58% | 7.02% |
| 0.005 | 498 | 8 | 490 | 11 | 1.61% | 42.11% | 3.09% |
| 0.002 | 2,139 | 18 | 2,121 | 1 | 0.84% | 94.74% | 1.67% |

Lower cutoffs identify more recorded removals but generate many more false alerts. These are **illustrative decision thresholds**, not optimized operating points. The low event count and false-alert burden do not establish a useful deployment rule.

## Limitations and further work

Feature selection followed repeated inspection of the historical test period, including comparison of different predictor sets. The observed nine-feature improvements are therefore **exploratory**, not independent evidence of future predictive improvement. A new, previously unseen period is needed to evaluate the frozen model and any chosen alert threshold.

Only nineteen distinct removals remain in the eligible test set, compared with thirty-one after source screening. Missing or delisted price histories, reviewed identity/date boundaries, approximate source dates and incomplete benchmark coverage narrow the sample. Repeated monthly security observations and removals clustered by date are dependent, so row counts should not be interpreted as independent events.

Downloaded prices and retrospective membership reviews do not certify what information was available at each historical date. Close-based price returns omit dividend reinvestment; corporate-action adjustments and later vendor revisions can change results. Financial context describes historical prices and trading activity, not index committee intentions.

Useful next research would define an alert budget and evaluate a **frozen** nine-feature specification on genuinely uninspected later data, with more distinct removal events and improved historical source coverage. Reporting uncertainty by time period and security group would help assess robustness. Further feature searching on the same inspected test set would not provide independent confirmation.

**IndexLens reports reproducible exploratory research, not established removal-forecast performance.**
