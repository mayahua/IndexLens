# Evidence explanations

`POST /api/v1/research/explain` asks OpenAI to explain a recorded event, a momentum ranking or a saved research run. Application services supply all numerical facts. Responses return the evidence, its SHA-256 hash, checked scalar claims, model name, real/synthetic label and interpretation limits. GET endpoints continue to work when OpenAI credentials or service access are unavailable.

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

Set the database URL using the [database guide](database.md). The example starts a local server; an explanation request can incur OpenAI usage charges. There is no credential check or paid request during startup. The model must support Responses structured output and be accessible to the configured account.

| Variable | Default | Bounds |
|---|---|---|
| `OPENAI_API_KEY` | Unset | Required only for explanations |
| `INDEXLENS_OPENAI_MODEL` | `gpt-5-mini` | Model ID, up to 100 characters |
| `INDEXLENS_OPENAI_TIMEOUT_SECONDS` | `30` | 1–60 seconds for the entire provider operation |
| `INDEXLENS_OPENAI_RETRIES` | `1` | 0–2 retries, hence at most 3 attempts |
| `INDEXLENS_OPENAI_MAX_OUTPUT_TOKENS` | `3000` | 512–4000, including reasoning tokens |

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

Evidence retains every rank, unavailable-value reason, feature/window date, input provenance, review basis and mapping bounds, original source intervals and optional same-date membership context. Features remain `observed-20-v2`. **Momentum ranks describe past returns, not removal probabilities.** Incomplete rankings have `evidence.interpretation.status: limited`; the explanation describes their gaps.

### Saved research run

Discover `run_id` from `GET /api/v1/research/runs` and inspect `GET /api/v1/research/runs/{run_id}`:

```sh
curl 'http://127.0.0.1:8000/api/v1/research/explain' \
  -H 'Content-Type: application/json' \
  -d '{"target":{"kind":"run","run_id":"experiment-001"}}'
```

The explanation reuses that exact startup snapshot, including report/input hashes, counts, metrics, undefined reasons, split boundaries, model/feature versions, coverage and limitations. It performs no retraining. Saved-run explanations need no database access. An unavailable or inconclusive evaluation has `evidence.interpretation.status: limited` and retains its unmet requirements. Evaluation completion does not establish predictive validity.

## Evidence and validation

`claims` is a list of `{reference, value_json}` objects. A reference is a JSON Pointer into the returned `evidence`; for example `/result/summaries/train/eligible/observations`. `value_json` is the exact compact JSON representation of that scalar: `"8"` for an integer eight, `"null"` for an unavailable value, or `"\"0.1\""` for a decimal string. String identities retain leading zeros. References to whole objects and invented fields are rejected.

The model selects claims before drafting prose with citation placeholders such as `{{c0}}`. The application checks each pointer against the supplied evidence, compares its value exactly, and replaces the placeholder with the field path and value. It rejects uncited claims, unknown placeholders, bare numerical digits, percentages and common spelled-out numbers in the draft. Rounded values, unit conversions and newly calculated numbers are rejected. Every cited claim must be used.

These checks are separate from JSON/schema validation. They establish that cited scalar values match the supplied evidence; they **do not prove that qualitative prose is accurate**, that a causal interpretation is justified, or that the source itself is correct. The returned `validation.qualitative_accuracy` is `not_established`. Inspect the evidence and the interpretation together. Source strings are quoted evidence, not trusted instructions or independently verified assertions.

The model receives the bounded question, evidence JSON and a catalogue of copyable scalar claims as user content, with separate application instructions. The catalogue supplies exact references and JSON values that fit the claim fields; returned claims still undergo independent checks. It has no SQL, filesystem or external tools. The application always adds the data label and the applicable event/ranking/research boundary to `limitations`, independent of the model's prose. Source limitations remain inside the evidence. Nulls and reasons are retained; missing values are never replaced with zero.

## Bounds and errors

Request bodies are limited to 4 KiB before JSON parsing, including streamed bodies. Questions are at most 200 characters. Evidence is limited to 128 KiB and the combined instructions, question, evidence and claim catalogue to 140 KiB. Oversized evidence is rejected intact. Event lookup supports up to 200 source events on the selected date; ranking evidence supports up to 200 intervals per identity. Output has at most twelve claims, a 4,000-character draft and the configured token limit. These limits are independent of the general API response cap.

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

A separate live OpenAI Responses smoke test passed on 19 October 2026 using `gpt-6-luna` for a saved research run. The endpoint returned HTTP 200; all eight claims were independently checked against the supplied evidence, the saved-report and evidence hashes matched, and the prose was reviewed against the recorded results and limitations. GET endpoints remained usable. This verifies the tested response; it does not establish general explanation accuracy or predictive validity.

To repeat the live smoke check, start the server with a configured key and send one supported request above against a discovered target. A live success requires HTTP 200 from the actual provider, checked claims, correct real/synthetic labelling and manual comparison of the prose and every claim with `evidence`. Save the request, response, evidence hash, model, date and review outcome privately. A mock success, a missing-key response or an unreviewed provider response does not satisfy that check.

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
