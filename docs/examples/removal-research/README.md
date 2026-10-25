# Removal research examples

[metrics.json](metrics.json) is a full-precision, machine-readable record of the supported historical S&P 500 monthly removal experiment. It contains the shared training and test cohort summaries, the constant baseline, matched three-feature and primary nine-feature model metrics, five illustrative probability cutoffs, model settings and versions, limitations, and source-report and observation-artifact hashes. These are IndexLens-calculated numerical results, not OpenAI-generated commentary.

The [research guide](../../research.md) documents the methodology, reproduction commands, metric definitions and limitations. Its [Experiment results](../../research.md#experiment-results) section is the main presentation of the model comparison and cutoff grid; this JSON file retains exact values and provenance rather than repeating those tables here.

This artifact is not a standalone reproduction package. Reproducing the results requires the corresponding reviewed membership and price sources, benchmark, ingestion and review records, and numerical-library environment. Provider data is not redistributed, and later historical-data revisions can change results. Repeated inspection of the test period, rare removals and source-coverage gaps limit claims of predictive validity.

Two reviewed explanations demonstrate the application's citation and presentation contract:

- [Model comparison](model-comparison/README.md): shared cohorts, the constant baseline and both logistic models, selected-cutoff performance and research limitations.
- [FMC financial context](company-financial-context/README.md): dated company indicators, fitted and supplementary context, a saved probability and retrospective limitations.

Each guide links to verbatim application-rendered prose, the original structured draft, exact citations, public evidence and provenance. Both use `verified-display-v5`. Public evidence omits two declared operational fields and has its own hash; original evidence fingerprints remain recorded separately. Automated numerical validation and manual qualitative review have distinct scopes, explained in each guide. The [local verifier](verify.py) reproduces both renderings with the installed application and no provider call:

```sh
python docs/examples/removal-research/verify.py
```
