# Evidence explanations

`POST /api/v1/research/explain` asks OpenAI to explain saved removal research, a company's dated financial context, a recorded event or a momentum ranking. Application services supply all numerical facts. Responses return the evidence, its SHA-256 hash, checked scalar claims, model name, real/synthetic label and interpretation limits. GET endpoints continue to work when OpenAI credentials or service access are unavailable.

## Configuration and startup

Install `.[dev]` and follow [API startup](api.md#start-locally). Export `OPENAI_API_KEY` through the local shell or a secret manager. The application reads exported variables; it does not load `.env`. Keep the key out of configuration JSON, requests, shell-history literals and Git. For an interactive shell, Python can prompt for the key without displaying it and then start the server:

```sh
export INDEXLENS_API_CONFIG='path/to/api.json'
export INDEXLENS_OPENAI_MODEL='gpt-6-luna'
python - <<'PY'
import getpass
import os
import uvicorn
os.environ['OPENAI_API_KEY'] = getpass.getpass('OpenAI API key: ')
uvicorn.run('indexlens.api.app:create_app', factory=True, host='127.0.0.1', port=8000, access_log=False)
PY
```

Set the database URL using the [database guide](database.md). The example starts a local server; an explanation request can incur OpenAI usage charges. There is no credential check or paid request during startup. The model must support Responses structured output and be accessible to the configured account. Set `INDEXLENS_OPENAI_MODEL` to the preferred compatible model before starting or restarting the server; requests do not override it. A published example's model choice does not change this setting for other users.

| Variable | Default | Bounds |
|---|---|---|
| `OPENAI_API_KEY` | Unset | Required only for explanations |
| `INDEXLENS_OPENAI_MODEL` | `gpt-6-luna` | Model ID, up to 100 characters |
| `INDEXLENS_OPENAI_TIMEOUT_SECONDS` | `60` | 1–180 seconds for the entire provider operation |
| `INDEXLENS_OPENAI_RETRIES` | `1` | 0–2 retries, hence at most 3 attempts |
| `INDEXLENS_OPENAI_MAX_OUTPUT_TOKENS` | `3000` | 512–8000, including reasoning tokens |
| `INDEXLENS_OPENAI_REASONING_EFFORT` | Unset (provider default) | Validated model-specific effort; see below |

SDK retries are disabled. The application retries connection errors, timeouts, HTTP 408/409/429 and server errors, with 0.25 then 0.5 seconds of backoff inside the total deadline. Provider retry headers cannot extend that deadline. Refusals, schema errors and unsupported claims are not retried. Invalid explanation configuration affects POST requests only.

The integration uses `AsyncOpenAI.responses.parse`, a Pydantic output schema, `store=False`, disabled input truncation and no tools. The endpoint is fixed to OpenAI's HTTPS API. These choices follow the official [structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), [Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create), [Python SDK documentation](https://developers.openai.com/api/reference/python) and [model capabilities](https://developers.openai.com/api/docs/models/gpt-6-luna). Provider availability still requires a separate live check.

## Request forms and identifier discovery

Every request contains `target` and an optional `question`. The supported question strings are exactly `Summarize the evidence.` (default) and `Explain the limitations.`. Other questions return `422 unsupported_question` before provider access. This deliberately limited interface does not answer forecasts, causes of committee decisions, investment questions or arbitrary research questions. Extra fields are rejected.

All identifiers and dates below are synthetic illustrations. Substitute discovered IDs and supported dates.

### Recorded event

First call `GET /api/v1/indices`, then `GET /api/v1/indices/{index_id}/events?start=2026-01-01&end=2026-01-31`. Follow pagination if needed. Copy the chosen event's `security_id`, `source_record_position`, `event_type` and `event_date`, and retain the selected `index_id`:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/explain' \
  -H 'Content-Type: application/json' \
  -d '{"target":{"kind":"event","index_id":"sample","security_id":"v1-s1","source_record_position":1,"event_type":"REMOVE","event_date":"2026-01-10"}}'
```

These fields identify one event on one source interval. The security must belong to the index's published source version. A ticker alone is not accepted. The response preserves original fields, interval dates, record hash, approximation flags, creation-date guards, source findings and coverage. Source events do not establish official causes or information availability at the event date.

### Momentum ranking

Discover a review from `GET /api/v1/reviews`, then inspect `GET /api/v1/rankings?review_id=sample-review&reference_date=2026-05-29`. Use that review and date:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/explain' \
  -H 'Content-Type: application/json' \
  -d '{"target":{"kind":"ranking","review_id":"sample-review","reference_date":"2026-05-29","membership":true},"question":"Explain the limitations."}'
```

`membership` defaults to false. When true, the common observation date must be supported by membership coverage. Explanations support the complete declared universe of up to ten identities. A larger universe is rejected, never reduced to a top-ten sample. This bound applies only to explanations; the existing ranking and research populations remain unchanged.

Evidence retains every rank, unavailable-value reason, feature/window date, input provenance, review basis and mapping bounds, original source intervals and optional same-date membership context. The current ranking uses `observed-20-v2` features. **Momentum ranks describe past returns, not removal probabilities.** Incomplete rankings have `evidence.interpretation.status: limited`; the explanation describes their gaps.

### Saved research run

Discover `run_id` from `GET /api/v1/research/runs` and inspect `GET /api/v1/research/runs/{run_id}`:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/explain' \
  -H 'Content-Type: application/json' \
  -d '{"target":{"kind":"run","run_id":"experiment-001"}}'
```

The explanation reuses that exact startup snapshot, including report/input hashes, counts, metrics, undefined reasons, split boundaries, model/feature versions, coverage and limitations. It performs no retraining. Saved-run explanations need no database access. An unavailable or inconclusive evaluation has `evidence.interpretation.status: limited` and retains its unmet requirements. Evaluation completion does not establish predictive validity.

For a cutoff comparison, discover its `threshold` through `GET /api/v1/research/runs`, then select its distinct `run_id`. Reports can be created using the [public cutoff command](research.md#evaluate-cutoffs-from-a-saved-report) and [registered before restarting the API](api.md#identifier-discovery). Evidence retains that report's `result.evaluation.threshold`, cutoff-dependent metrics and any `result.evaluation.derivation` source references and exploratory flags. The current research command default does not override saved thresholds. A derived evaluation reuses saved probabilities; it is not a newly fitted model or independent validation. Provider instructions request the selected threshold, relevant model metrics and limitations, including the exploratory derivation. Exact claim checks reject a threshold or metric copied from another report.

## Evidence and validation

`claims` is a list of `{reference, value_json}` objects. A reference is a JSON Pointer into the returned `evidence`; for example `/result/summaries/train/eligible/observations`. `value_json` is the exact compact JSON representation of that scalar: `"8"` for an integer eight, `"null"` for an unavailable value, or `"\"0.1\""` for a decimal string. String identities retain leading zeros. References to whole objects and invented fields are rejected.

The model selects claims before drafting prose with citation placeholders such as `{{c0}}`. The application checks each pointer against the supplied evidence, compares its value exactly, and replaces the placeholder with an application-formatted value and a short marker such as `[1]`. It rejects uncited claims, unknown placeholders, bare numerical digits, percentages and common spelled-out numbers in the draft. Rounded values, unit conversions and newly calculated numbers are rejected. Every cited claim must be used.

These checks are separate from JSON/schema validation. They establish that cited scalar values match the supplied evidence; they **do not prove that qualitative prose is accurate**, that a causal interpretation is justified, or that the source itself is correct. The returned `validation.qualitative_accuracy` is `not_established`. Inspect the evidence and the interpretation together. Source strings are quoted evidence, not trusted instructions or independently verified assertions.

The model receives the bounded question, evidence JSON and a catalogue of copyable scalar claims as user content, with separate application instructions. The catalogue supplies exact references and JSON values that fit the claim fields; returned claims still undergo independent checks. It has no SQL, filesystem or external tools. The application always adds the data label and the applicable event/ranking/research boundary to `limitations`, independent of the model's prose. Source limitations remain inside the evidence. Nulls and reasons are retained; missing values are never replaced with zero.

## Bounds and errors

Request bodies are limited to 4 KiB before JSON parsing, including streamed bodies. Questions are at most 200 characters. Evidence is limited to 128 KiB and the combined instructions, question, evidence and claim catalogue to 140 KiB. Oversized evidence is rejected intact. Event lookup supports up to 200 source events on the selected date; ranking evidence supports up to 200 intervals per identity. Output has at most thirty-two claims, a 6,000-character draft and the configured token limit. These limits are independent of the general API response cap.

| HTTP status | Error code or meaning |
|---|---|
| `200` | Explained evidence; inspect `interpretation.status` for limited results |
| `404` | Unknown configured identifier or `unknown_event` |
| `413` | `request_too_large` |
| `422` | Invalid input, `unsupported_question`, `evidence_too_large`, `insufficient_evidence`, identity/date mismatch or `openai_refusal` |
| `502` | `openai_failure`, `openai_incomplete`, `openai_malformed_output`, `invalid_evidence_reference` or `invalid_claim` |
| `503` | Existing database/evidence errors; `openai_not_configured`, `openai_configuration`, `openai_access_denied` or `openai_rate_limited` |
| `504` | `openai_timeout` |

Errors return the existing `{error: {code, message}}` envelope. Provider bodies, refusal text, credentials and raw exception details are not returned or logged by the application. Keep verbose SDK/transport logging disabled when handling private evidence. Existing GET endpoints do not call OpenAI.

## Verification

Ordinary pytest and CI use mocked OpenAI HTTP transport, including successful structured responses, inaccurate claims, unsupported questions, unavailable evidence and provider failures. Synthetic database fixtures use 2025–2026 dates. No paid call is part of the test suite.

To verify a live explanation, start the server with a configured key and send one supported request above against a discovered target. A live success requires HTTP 200 from the actual provider, checked claims, correct real/synthetic labelling and manual comparison of the prose and every claim with `evidence`. Save the request, response, evidence hash, model, date and review outcome privately. Mock success, a missing-key response or an unreviewed provider response do not establish analyst-commentary quality.

## Security

IndexLens does not save the API key to a file or database, include it in API responses, or write it to application logs. It reads the key from the environment and passes it to the OpenAI SDK for authentication.

Take care with possible exposure outside IndexLens:

- **Shell history:** entering the key through a hidden `read` prompt normally keeps it out of command history.
- **Memory:** when exported from a shell, the key remains in that shell's environment, and the API server inherits a copy. Other programs launched from the same shell can also inherit it.
- **Elsewhere:** pasting may leave the key in the clipboard or clipboard history. System diagnostics or memory storage are outside IndexLens's control.

After stopping the API server, remove an exported key from the current shell with:

```sh
unset OPENAI_API_KEY
```

This does not remove copies already inherited by running processes. Clear the clipboard separately if the key was pasted.

## Company financial context

The existing POST endpoint also accepts this synthetic request shape:

```json
{"target":{"kind":"financial_context","security_id":"v1-s0","review_id":"sample","reference_date":"2026-07-31","benchmark_id":"sp500","run_id":"example-saved-run"}}
```

`benchmark_id` and `run_id` are optional. Discover security, stock-review and run IDs through the existing GET routes; benchmark IDs come from operator configuration. Inspect the financial-context GET response first. The model receives computed returns, market differences, dollar activity, drawdown, volatility, missingness and limitations. Its instructions request connected financial commentary while separating descriptive observations from fitted predictors and index decisions.

When `run_id` is supplied, identity, membership source hash and real/synthetic kind must match, and the reference date must match exactly one saved prediction. The comparison uses `saved_probability >= saved_threshold`, including equality. No new probability is fitted. Missing or ambiguous predictions return 422. The join uses a common date: a weekend month-end prediction cannot be joined to a different observation date automatically. Choose a supported common date or request financial context separately. Retrospective evaluation metrics are labelled separately from historically dated prices; subsequent realised labels are excluded from the attached prediction.

Evidence includes exact indicator values, compact window bounds/counts/date hashes, source hashes/retrieval times, the public input hash and a hash of the complete GET snapshot. Underlying dated inputs remain in GET output. Compaction removes repeated row-level provenance from the model input while retaining source identity and snapshot verification. Existing evidence/input limits still apply.

Claims use the existing exact JSON Pointer/scalar check and citation-only numeric rules. Incorrect numbers and references are rejected. These checks do not verify all qualitative comparisons or causal language; manual prose review remains necessary. Missing values and interpretive limits are supplied even if the model omits them from its draft. Ordinary tests use mocked Responses transport. The [FMC showcase](examples/removal-research/company-financial-context/README.md) supplies a separately generated and manually reviewed financial-context example, with its saved draft, exact citations and public evidence.

## Research comparison and company evidence

For an evaluated `monthly-removal-nine-v1` run, the model-level explanation uses the saved constant training-prevalence baseline, the matched logistic comparator and the primary nine-feature logistic regression. The returned model labels, average precision and Brier scores come from the saved report; the requested cutoff supplies the classification and alert metrics. See the [research guide](research.md#experiment-results) for their three-way comparison.

A compatible saved research report may also be attached to a `financial_context` target. The join checks source identity, approved stock and benchmark reviews, source hashes, exact prediction/observation dates, the nine fitted values and their saved input fingerprint. An old or incompatible report without this join contract is not attachable; saved-run summaries remain separately queryable. The joined evidence identifies `saved_prediction.fitted_features`, `fitted_values`, `supplementary_indicators` and the selected probability and cutoff. Its aggregate evaluation is retrospective; subsequent realised labels are not attached to the dated company prediction.

## Readable verified presentation

`verified-display-v5` preserves exact `claims`, `evidence` and `evidence_sha256`, and returns the original structured `raw_draft` plus a `citations` list. Each marker resolves to its original JSON Pointer, exact `value_json` and displayed value. Long limitation strings, the approved saved-cutoff derivation identifier and the exact approved `/saved_prediction/timing` statement appear as citation-only markers in prose; their exact text remains in citations and evidence. Source text remains untrusted.

Formatting is deterministic: probabilities, precision/recall/F1, returns and daily volatility use two decimal places as percentages; excess returns use percentage points; average precision uses five decimal places; Brier score uses eight; dollar volume uses two decimal places in USD per session; ratios use three; cohort counts have thousands separators and alert counts below twenty use words; ISO dates become day/month-name/year. Unknown numerical fields retain up to six significant digits. Raw precision is always available in the citation. Approved model and predictor labels render as readable text at their designated evidence paths, including model labels nested under `saved_research.result.model_labels`; the exact original strings remain in claims and citations. The claim catalogue previews these displays. At `/result/symbol`, short uppercase ticker symbols with an optional dot or hyphen suffix render without quotation marks. Other strings retain their quoted form. The provider still cannot round, convert units or introduce uncited numbers.

For evaluated `monthly-removal-nine-v1` run targets, the application requires cited labels, average precision and Brier score for all three comparators, plus the selected cutoff, primary alert/true-positive/false-positive/missed-event counts, precision and recall. Missing coverage returns `502 incomplete_comparison`, even if the JSON is schema-valid. Instructions prioritise the requested training start, training outcome cutoff, test dates, shared eligible cohort sizes, positive counts and baseline training prevalence before optional predictor details. Predictor families can be summarised without listing every identifier. Connected paragraphs cover the research question, data, comparison, prediction results, interpretation and limitations. The Brier explanation distinguishes class frequency from squared-error contributions: rare removals allow a small average error even for a weak constant forecast. This verifies the presence and exact values of the comparison; qualitative comparisons and causal implications still need human review.

Instructions are selected by request target. For `financial_context`, company identity, observation date and a connected financial assessment come first. The claim catalogue prioritises returns, benchmark-relative performance, volatility, drawdown, dollar activity, the fitted-predictor boundary and any saved probability and cutoff. The complete evidence remains claimable. Attached research evaluation supports a brief later discussion of limitations, with at most one optional aggregate metric; the full comparison remains part of the `run` target. These narrative priorities guide generation and require manual review.

For supported company joins, `saved_prediction` contains the exact original probability, selected threshold, classification, predictor names/values and observation date. `supplementary_indicators` identifies the remaining context. The saved outcome label is excluded. The attached aggregate research evaluation is explicitly retrospective. Historical input dates do not establish point-in-time availability or the index committee's reasoning.

## Explicit reasoning and generation records

Set `INDEXLENS_OPENAI_REASONING_EFFORT=medium` or `high` with `INDEXLENS_OPENAI_MODEL=gpt-6-astra` for explicit reasoning. The application validates known model/effort combinations before provider access. Astra and the supported Sol aliases accept low/medium/high/xhigh/max; Luna also accepts none; GPT-5 Mini accepts minimal/low/medium/high. Unknown model IDs can still be used with reasoning unset; explicit reasoning requires updating the documented compatibility policy. API account access must be checked separately.

The [Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra) documents structured output and its reasoning efforts. The [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) explains that reasoning consumes the output allowance and can exhaust it before visible prose. The bounded application allowance may therefore yield `openai_incomplete`; incomplete/schema/claim failures do not automatically trigger another generation. The ordinary API deadline defaults to 60 seconds and is independently configurable through `INDEXLENS_OPENAI_TIMEOUT_SECONDS`, from 1 to 180 seconds. An offline showcase can select a longer deadline in its own process. The deadline covers the entire provider operation, including retries and cleanup; the 0–2 retry bounds remain unchanged. Standard service tier is explicit.

`generation` records response ID, requested and actual model information, requested and provider-reported effort (null if unreported), creation time, usage, service tier and token/deadline/retry settings. It does not invent an effective effort when the provider omits it. Capture original provider HTTP bytes separately when auditing a live example, including unsuccessful outputs. Keep raw receipts private until checked for secrets and provider-data restrictions.

The [supported research examples](examples/removal-research/README.md) present numerical results and reviewed explanations for [model comparison](examples/removal-research/model-comparison/README.md) and [FMC financial context](examples/removal-research/company-financial-context/README.md). Both preserve the original draft and use application-side `verified-display-v5` rendering. Their guides describe exact citation checks, manual review and reproduction limits; provenance distinguishes original evidence fingerprints from public evidence hashes after declared operational-field omissions. Mocked responses establish software behaviour, not prose quality.
