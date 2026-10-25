# Model comparison

Application-side rendering of the original generation using `verified-display-v5`. The generated explanation below has no manual prose edits.

[Example guide](README.md) · [Structured explanation](explanation.json) · [Evidence](evidence.json) · [Provenance](provenance.json)

## Generated explanation

This retrospective research examines index removals within 30 [1] calendar days after monthly reference observations. The requested training reference start was 31 January 2022 [2], with training outcomes bounded by 31 December 2024 [3]; these are not necessarily the first eligible training observation or the last training reference date. Test reference dates ran from 31 January 2025 [4] through 31 July 2026 [5]. All comparators use the same eligible training and test cohorts: 15,289 [6] training observations with 25 [7] positive cases, and 9,252 [8] test observations with 19 [9] positive cases. Identity reviews were retrospective, and missing or conflicting windows remained unavailable. Prices were provider-returned Yahoo closing quotes with library adjustments disabled, not certified original as-traded prices or total returns; later revisions cannot be ruled out [10].

The constant baseline [11] assigned the constant training-prevalence probability of 0.16% [12], recording average precision of 0.00205 [13] and a Brier score of 0.00204957 [14]. The matched original three-feature model [15] recorded average precision of 0.02854 [16] and a Brier score of 0.00203863 [17]. The primary nine-feature model [18] had higher average precision of 0.03683 [19] and a slightly lower Brier score of 0.00202765 [20] than the comparators. Average precision measures ranking across recall levels; Brier score measures average squared probability error, not proof of calibration. With rare removals, assigning low probabilities to almost every observation can yield a small Brier score, so that score alone does not establish useful discrimination or forecasting performance.

At the selected probability cutoff of 5.00% [21], the primary model issued seven [22] removal alerts, including one [23] true positive and six [24] false positives, while it missed eighteen [25] removals. Precision was 14.29% [26] and recall was 5.26% [27]: most alerts were false alarms, and most removals were missed. This is a saved-probability cutoff evaluation [28]. It reused saved labels and probabilities without changing the fitted model or its configuration; the cutoff comparison is exploratory and does not establish an optimal cutoff [29].

Rare events limit interpretation. Repeated securities, clustered removals and overlapping outcome windows create dependent observations and can duplicate or miss events [30]. Missing acquired or delisted histories and uneven identity coverage restrict generalization beyond the evaluated population [31]. Feature selection followed repeated inspection of the test period, so the apparent comparative gains remain exploratory rather than independent predictive confirmation [32].

## Exact citations

Each JSON Pointer resolves within [evidence.json](evidence.json). Values below retain the original JSON type and precision; a marker with an empty display keeps its exact text here.

- [1] `/result/split/label_horizon_days` = `30`
- [2] `/result/split/train_reference_start` = `"2022-01-31"`
- [3] `/result/split/train_outcome_cutoff` = `"2024-12-31"`
- [4] `/result/split/test_reference_start` = `"2025-01-31"`
- [5] `/result/split/test_reference_end` = `"2026-07-31"`
- [6] `/result/summaries/train/eligible/observations` = `15289`
- [7] `/result/summaries/train/eligible/positive_observations` = `25`
- [8] `/result/summaries/test/eligible/observations` = `9252`
- [9] `/result/summaries/test/eligible/positive_observations` = `19`
- [10] `/result/review_limitations` = `"Retrospective continuity between dated same-CIK common/ordinary listing anchors. Consistent periodic anchors are interpolated with maximum 550-day gaps. Where an October 2026 SEC issuer index still lists the ticker, a matching filing since 16 April 2025 supports extension through 31 July 2026. Explicit manual ticker/name/spin-off decisions and retained prior decisions are recorded separately. Citigroup uses two issuer common-dividend anchors and is an explicit exception to the periodic-class rule. This is not a daily legal-history or point-in-time information certification. Later evidence is used for retrospective eligibility, never as a numerical predictor. Missing or conflicting window data remain unavailable. Pinned Yahoo quote Close and volume with library adjustments disabled; Adj Close excluded. This is a retrospective provider-returned basis, not certified original as-traded prices or total returns. Provider adjustments and later revisions cannot be ruled out."`
- [11] `/result/model_labels/baseline` = `"Constant-probability baseline"`
- [12] `/result/evaluation/baseline/probability` = `0.0016351625351559945`
- [13] `/result/evaluation/baseline/metrics/average_precision` = `0.002053610030263727`
- [14] `/result/evaluation/baseline/metrics/brier_score` = `0.0020495678144134893`
- [15] `/result/model_labels/matched_three` = `"Three-feature logistic regression"`
- [16] `/result/evaluation/matched_three/metrics/average_precision` = `0.028536669582977098`
- [17] `/result/evaluation/matched_three/metrics/brier_score` = `0.0020386265975106655`
- [18] `/result/model_labels/logistic` = `"Nine-feature logistic regression"`
- [19] `/result/evaluation/logistic/metrics/average_precision` = `0.036832804363353844`
- [20] `/result/evaluation/logistic/metrics/brier_score` = `0.00202764592763664`
- [21] `/result/evaluation/threshold` = `0.05`
- [22] `/result/evaluation/logistic/metrics/predicted_removals` = `7`
- [23] `/result/evaluation/logistic/metrics/true_positives` = `1`
- [24] `/result/evaluation/logistic/metrics/false_alarms` = `6`
- [25] `/result/evaluation/logistic/metrics/false_negatives` = `18`
- [26] `/result/evaluation/logistic/metrics/precision` = `0.14285714285714285`
- [27] `/result/evaluation/logistic/metrics/recall` = `0.05263157894736842`
- [28] `/result/evaluation/derivation/kind` = `"saved_probability_cutoff_evaluation"`
- [29] `/result/limitations/9` = `"This exploratory evaluation reuses saved labels and probabilities. The fitted model and its configuration are retained from the source. Comparisons on inspected test data are not independent validation and do not establish an optimal cutoff or predictive usefulness."`
- [30] `/result/limitations/3` = `"Source report limitation: Monthly 30-day windows can count one event twice and miss other events. Repeated securities and clustered removals are dependent observations."`
- [31] `/result/limitations/2` = `"Source report limitation: Missing acquired/delisted histories and uneven mapping evidence select the evaluated population; results do not generalize to all members."`
- [32] `/result/limitations/6` = `"Source report limitation: Feature selection followed repeated inspection of this historical test period. Matched-cohort comparisons are exploratory and do not establish independent predictive improvement. Rare removals make small Brier scores possible even for a weak constant baseline."`

## Application limitations

- Real source evidence with the stated coverage and limitations.
- Predictive validity remains unestablished.
- References and scalar values were checked against the supplied evidence. Schema and claim checks do not establish the accuracy of qualitative prose.
