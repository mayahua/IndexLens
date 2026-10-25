# FMC financial analysis

Application-side rendering of the original generation using `verified-display-v5`. The generated explanation below has no manual prose edits.

[Example guide](README.md) · [Structured explanation](explanation.json) · [Evidence](evidence.json) · [Provenance](provenance.json)

## Generated explanation

FMC [1], observed on 28 February 2025 [2], showed pronounced price weakness: the recent return of -34.55% [3] was close in magnitude to the medium-horizon return of -37.55% [4]. Aligned benchmark excess returns of -32.63 percentage points [5] and -36.26 percentage points [6], respectively, show substantial underperformance. Daily unannualized volatility of 7.69% [7], stock-to-benchmark volatility of 9.407× [8], and drawdown from the trailing closing high of -44.61% [9] underscore the severity of the observed price conditions. Recent average dollar volume of $167,046,520.31 per session [10] exceeded the longer-window average of $108,780,356.38 per session [11]. These overlapping averages compare recent activity with a broader baseline, not consecutive-period growth, and increased activity does not identify selling pressure. These indicators do not establish fundamentals, index eligibility, committee intent or future performance [12].

The reported indicator windows have values, with stock coverage of 121 [13] observations and benchmark coverage of 121 [14] observations. Entirely absent sessions are not filled, and volatility can span gaps [15]. The basis is retrospective provider-returned Yahoo closing prices and volume with library adjustments disabled and adjusted closes excluded—not certified as-traded prices or total returns. Provider adjustments and later revisions remain possible [16].

Fitted inputs include short-term returns (20 sessions) [17], market-relative returns (20 sessions) [18], daily return volatility (20 sessions) [19] and average dollar trading volume (20 sessions) [20], representing return, benchmark-relative, volatility and dollar-activity families. By contrast, longer-term returns (120 sessions) [21] and drawdown from the trailing closing high (120 sessions) [22] are supplementary rather than fitted predictors. The observed conditions are not evidence of which predictor drove the saved probability.

Separately, the saved removal probability over the following 30 [23] calendar days was 6.85% [24]; it exceeded the selected classification cutoff of 5.00% [25]. The attached evaluation is retrospective, not information known at the observation date [26]. Reuse of previously inspected test data makes it exploratory rather than independent validation of the cutoff or predictive usefulness [27]. Missing histories and uneven identity mapping limit generalization [28], and predictive validity remains unestablished [29].

## Exact citations

Each JSON Pointer resolves within [evidence.json](evidence.json). Values below retain the original JSON type and precision; a marker with an empty display keeps its exact text here.

- [1] `/result/symbol` = `"FMC"`
- [2] `/result/observation_date` = `"2025-02-28"`
- [3] `/result/indicators/return_20/value` = `"-0.3455125784531585565016174243201966"`
- [4] `/result/indicators/return_60/value` = `"-0.3755288300796722319169788837400159"`
- [5] `/result/indicators/excess_return_20/value` = `"-0.3262955373595421142938082956868215"`
- [6] `/result/indicators/excess_return_60/value` = `"-0.3626185219881019512846426811795176"`
- [7] `/result/indicators/volatility_20/value` = `"0.07687177437291810944306679757458222"`
- [8] `/result/indicators/relative_volatility_20/value` = `"9.407444258207730884326810879612690"`
- [9] `/result/indicators/drawdown_120/value` = `"-0.4461122785262557255693656704678585"`
- [10] `/result/indicators/average_dollar_volume_20/value` = `"167046520.313549037442505"`
- [11] `/result/indicators/average_dollar_volume_60/value` = `"108780356.3771247843319316666666667"`
- [12] `/interpretation/boundary` = `"Descriptive indicators are separate from the saved model's fitted predictors. Close-based price returns exclude dividend reinvestment. Provider revisions and corporate-action adjustments can affect historical values; retrieval dates do not certify point-in-time availability. Windows count observed dates. Entirely absent sessions are not filled; volatility can include returns across gaps and is unannualized. Closing price times volume is an approximate trading-activity measure, not transaction turnover or the official S&P liquidity eligibility test. The short dollar-volume mean overlaps the longer mean; their ratio compares recent activity with a broader baseline, not consecutive periods. Trailing drawdown is distance below a trailing closing high, not maximum peak-to-trough loss or a forecast. Volatility measures both gains and losses. Price indicators do not establish fundamentals, index eligibility, committee intentions or future performance. Addition criteria and existing-member removal decisions differ; committee discretion remains relevant."`
- [13] `/result/coverage/stock/observations` = `121`
- [14] `/result/coverage/benchmark/observations` = `121`
- [15] `/result/limitations/2` = `"Windows count observed dates. Entirely absent sessions are not filled; volatility can include returns across gaps and is unannualized."`
- [16] `/metadata/basis_review/description` = `"Pinned Yahoo quote Close and volume with library adjustments disabled; Adj Close excluded. This is a retrospective provider-returned basis, not certified original as-traded prices or total returns. Provider adjustments and later revisions cannot be ruled out."`
- [17] `/saved_prediction/fitted_features/0` = `"return_20"`
- [18] `/saved_prediction/fitted_features/5` = `"excess_return_20"`
- [19] `/saved_prediction/fitted_features/1` = `"volatility_20"`
- [20] `/saved_prediction/fitted_features/3` = `"average_dollar_volume_20"`
- [21] `/saved_prediction/supplementary_indicators/0` = `"return_120"`
- [22] `/saved_prediction/supplementary_indicators/1` = `"drawdown_120"`
- [23] `/saved_research/result/split/label_horizon_days` = `30`
- [24] `/saved_prediction/logistic_probability` = `0.06849076303418626`
- [25] `/saved_prediction/threshold` = `0.05`
- [26] `/saved_prediction/timing` = `"Saved evaluation metrics are retrospective, not facts known at the reference date."`
- [27] `/saved_research/result/limitations/9` = `"This exploratory evaluation reuses saved labels and probabilities. The fitted model and its configuration are retained from the source. Comparisons on inspected test data are not independent validation and do not establish an optimal cutoff or predictive usefulness."`
- [28] `/saved_research/result/limitations/2` = `"Source report limitation: Missing acquired/delisted histories and uneven mapping evidence select the evaluated population; results do not generalize to all members."`
- [29] `/saved_research/result/limitations/10` = `"Predictive validity remains unestablished."`

## Application limitations

- Real source evidence with the stated coverage and limitations.
- Descriptive indicators are separate from the saved model's fitted predictors. Close-based price returns exclude dividend reinvestment. Provider revisions and corporate-action adjustments can affect historical values; retrieval dates do not certify point-in-time availability. Windows count observed dates. Entirely absent sessions are not filled; volatility can include returns across gaps and is unannualized. Closing price times volume is an approximate trading-activity measure, not transaction turnover or the official S&P liquidity eligibility test. The short dollar-volume mean overlaps the longer mean; their ratio compares recent activity with a broader baseline, not consecutive periods. Trailing drawdown is distance below a trailing closing high, not maximum peak-to-trough loss or a forecast. Volatility measures both gains and losses. Price indicators do not establish fundamentals, index eligibility, committee intentions or future performance. Addition criteria and existing-member removal decisions differ; committee discretion remains relevant.
- References and scalar values were checked against the supplied evidence. Schema and claim checks do not establish the accuracy of qualitative prose.
