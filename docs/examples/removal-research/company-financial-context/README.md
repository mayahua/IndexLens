# FMC financial-context explanation

The [readable explanation](explanation.md) demonstrates company-first commentary about FMC as observed on 28 February 2025. It connects historical financial indicators, distinguishes fitted predictors from supplementary context and places a saved removal probability alongside the selected cutoff. Financial observations, model inputs and predictions remain separate from causal explanations.

The explanation is an application-side rendering of an original `gpt-6-astra` generation with requested and provider-reported `high` reasoning effort. The original prose and claim selection remain in `raw_draft` inside [explanation.json](explanation.json). The application validated the saved draft and rendered it with `verified-display-v5`; no manual prose rewrite or new generation was used. FMC renders without quotation marks, and the exact retrospective-timing statement remains in citation [26] instead of being inserted a second time in prose. This guide is editorial documentation, separate from the generated explanation.

## Evidence and findings

[evidence.json](evidence.json) contains the frozen financial-context indicators, units, window bounds and counts, coverage, stock and benchmark source hashes and retrieval times, input and context-snapshot hashes, fitted values and saved prediction. Its attached `nine-cutoff_0p05` research evaluation is the same saved comparison presented in the [model example](../model-comparison/README.md). [metrics.json](../metrics.json) retains the shared research results and cutoff grid. The source and snapshot hashes identify inputs; they are not a substitute for the underlying dated price records.

FMC's 20-session return was approximately −34.55% and its 60-session return −37.55%. Aligned S&P 500 price-index excess returns were −32.63 and −36.26 percentage points. Daily unannualized 20-session volatility was 7.69%, about 9.407 times benchmark volatility; drawdown from the trailing 120-session closing high was −44.61%. Recent 20-session average dollar activity of $167,046,520.31 per session exceeded the overlapping 60-session average of $108,780,356.38. These compare recent conditions with a broader window, not consecutive-period growth, and activity alone does not identify selling pressure. Exact values and units appear in citations [3]–[11] and their evidence objects.

The saved model used nine fitted predictors, all listed with exact values under `saved_prediction`. The explanation cites examples from their return, benchmark-relative, volatility and dollar-activity families. The 120-session return and drawdown are supplementary indicators, not fitted predictors. The saved 30-calendar-day removal probability was approximately 6.85%, above the selected 5.00% cutoff. This is a saved model prediction, not an observed FMC outcome; the example supplies neither feature attribution nor an explanation of committee motives. It is not an investment recommendation.

The data basis is retrospective provider-returned Yahoo closing prices and volume with library adjustments disabled and adjusted closes excluded. It does not certify original as-traded prices, total returns or point-in-time availability; later revisions and provider adjustments remain possible. Observed-session windows do not fill entirely absent sessions, volatility may span gaps, and close times volume is approximate activity. Trailing drawdown measures distance below a trailing closing high, not maximum peak-to-trough loss. The attached evaluation is retrospective and reuses an inspected test period. Rare events, dependent observations, missing histories and uneven identity mapping limit generalization and leave predictive validity unestablished.

## Inspect and verify

Each numbered marker in [explanation.md](explanation.md) has an exact JSON Pointer and `value_json` in its appendix. Resolve the pointer from the root of [evidence.json](evidence.json); inspect the adjacent unit and window fields for indicator claims. [explanation.json](explanation.json) records the raw draft, claims, displays, application limitations and validation status. [provenance.json](provenance.json) records the actual model and effort, generation time, draft and evidence fingerprints, rendering version and public artifact hashes.

Automated validation checks the schema, real-data label, exact scalar/reference agreement and use of every selected citation, and rejects bare numerical claims in the draft. It does not establish the correctness of qualitative comparisons, causal language or the underlying sources. Manual review assessed the company-first account, window comparisons, fitted-versus-supplementary distinction, retrospective timing, limitations and absence of realised-outcome, attribution or committee-causation claims. That review covers this example only; `validation.qualitative_accuracy` remains `not_established`.

Only the attached research's operational source-report location and embedded working-tree inventory were omitted from the public evidence, at the exact paths in the provenance record. All retained evidence, cited values and pointers are unchanged. Research review statuses describe retrospective input eligibility, not independent certification. The original evidence fingerprint is preserved separately from the public projection's hash. Recomputing the original full-bundle hash needs the retained original; underlying provider datasets and private operational records are not distributed. The compact context snapshot hash likewise identifies a larger source snapshot that these files alone cannot reconstruct.

With IndexLens and its dependencies installed, run from the repository root:

```sh
python docs/examples/removal-research/verify.py
```

This read-only check reproduces both public renderings and verifies claims, citations, limitations, presentation version and public hashes using the current application logic. It needs no API key, network access or database. It does not regenerate the provider response or authenticate its origin. See the [financial-context contract](../../../explanations.md#company-financial-context) and [research methodology](../../../research.md).
