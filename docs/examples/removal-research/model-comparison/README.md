# Model-comparison explanation

The [readable explanation](explanation.md) demonstrates how IndexLens turns saved S&P 500 monthly removal research into cited commentary. It compares a constant training-prevalence baseline, a matched three-feature logistic regression and the primary nine-feature logistic regression on shared eligible cohorts. The research question is whether the saved predictors help distinguish removals within the following 30 calendar days.

The explanation is an application-side rendering of an original `gpt-6-astra` generation with requested and provider-reported `medium` reasoning effort. The prose and claim selection are preserved in `raw_draft` inside [explanation.json](explanation.json). The application validated that draft and rendered its citation placeholders using `verified-display-v5`; no manual prose rewrite or new generation was used. The approved cutoff-derivation identifier remains exact in citation [28] and the evidence, while its redundant technical display is omitted from prose. This guide is editorial documentation, separate from the generated explanation.

## Evidence and findings

[evidence.json](evidence.json) contains the saved `nine-cutoff_0p05` evaluation: split boundaries, eligible and excluded populations, all three models' metrics, cutoff derivation, numerical-library versions and research limitations. It retains the source-report, observation-artifact, membership, review and input fingerprints. The derived report reuses saved labels and probabilities from the same research represented by [metrics.json](../metrics.json); it does not refit the model. The [research guide](../../../research.md#experiment-results) explains the methodology and cutoff grid.

The requested training reference start was 31 January 2022, with outcomes bounded by 31 December 2024. These bounds do not identify the first eligible observation or last training reference date. Test references span 31 January 2025 to 31 July 2026. The shared eligible training population contains 15,289 observations and 25 positive observations; the test population contains 9,252 and 19, respectively. These are repeated security observations, not independent companies or a complete index population.

Average precision is approximately 0.00205 for the baseline, 0.02854 for the three-feature model and 0.03683 for the nine-feature model; their Brier scores are 0.00204957, 0.00203863 and 0.00202765. At the selected 5.00% cutoff, the primary model has seven alerts: one true positive and six false positives, with eighteen missed removals. Precision is 14.29% and recall is 5.26%. Exact values are retained in citations [13]–[27]. Higher average precision and a slightly lower Brier score describe this retrospective comparison; rarity can produce small Brier scores even for weak forecasts.

Retrospective identity mapping, provider revisions, missing acquired or delisted histories and uneven source coverage restrict the evaluated population. Repeated securities, clustered removals and overlapping outcome windows create dependence. Feature and cutoff selection reused an inspected test period, so the result remains exploratory and does not establish independent predictive validity or an optimal cutoff.

## Inspect and verify

Start with the numbered markers in [explanation.md](explanation.md). Its citation appendix pairs each marker with an exact JSON Pointer and `value_json`. Resolve the pointer from the root of [evidence.json](evidence.json), including JSON Pointer escaping (`~1` for `/`, `~0` for `~`). [explanation.json](explanation.json) also records each formatted display, the original draft, application limitations and automated validation status. Percentages and other readable values are deterministic displays of the full-precision scalars.

Automated checks establish schema validity, real-data label agreement, exact scalar/reference equality, complete use of selected citations and required three-model numerical coverage. They reject bare numerical claims in the draft. They do not establish qualitative accuracy, source truth, calibration or causal explanations. Manual review assessed cohort and date wording, comparison direction, Brier interpretation, alert interpretation and preservation of the material limitations. That review applies to this example, not future generated responses; `validation.qualitative_accuracy` remains `not_established`.

[provenance.json](provenance.json) records the actual model, reasoning effort, generation time, original draft and evidence fingerprints, presentation version and public artifact hashes. The evidence omits only an operational source-report location and an embedded working-tree inventory, at the exact paths listed there. All retained values and citation paths are unchanged. Research review statuses describe retrospective input eligibility, not independent certification. Original full-bundle hashes identify the retained originals; public evidence has a separate hash because of those omissions. The public files reproduce rendering and cited numerical checks, not the original provider call or the full research calculation. Underlying provider datasets and private operational records are not distributed.

With IndexLens and its dependencies installed, run from the repository root:

```sh
python docs/examples/removal-research/verify.py
```

The read-only check revalidates both saved drafts through the current application logic and checks exact rendered prose, citations, application limitations, presentation version, public hashes and readable files. It needs no API key, network access or database. Recomputing the original full-evidence and original-response hashes requires the retained originals; hashes alone do not authenticate provider origin. See also the [FMC example](../company-financial-context/README.md) and [explanation contract](../../../explanations.md#evidence-and-validation).
