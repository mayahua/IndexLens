# IndexLens

IndexLens is a Python application for **historical S&P 500 constituent analysis and exploratory removal research**. It brings together source-data auditing, market-price ingestion, PostgreSQL, numerical modelling, REST APIs and evidence-backed AI explanations.

Recorded index removals provide a focused research question connecting historical membership and price evidence to dated observations, model comparisons and API-accessible results. Findings are retrospective and require independent evaluation before claims of predictive usefulness.

The repository supports two complementary workflows:

| Route | Purpose | Data |
|---|---|---|
| **Runnable local demonstration** | Exercise ingestion, database queries, features, saved research and API behaviour end to end | Deterministic, invented 2025–2026 membership and prices |
| **Historical research** | Examine recorded removals using reviewed S&P 500 membership, stock prices and a benchmark | Retrospective membership and Yahoo-returned price evidence, with documented exclusions |

## Capabilities

- **Historical membership:** Audits source CSVs, retains original fields and hashes, and queries membership on a date and recorded ADD/REMOVE events. Approximate dates and ambiguous identities remain visible.
- **Price ingestion:** Accepts offline CSV evidence and Yahoo stock/benchmark downloads. Coordinates bounded asynchronous workers with pacing, timeouts, retries and cancellation; caches source responses and batch-loads validated observations with duplicate and conflict checks.
- **Features and financial context:** Calculates returns, volatility and volume-based measures from reviewed price windows. Descriptive momentum rankings and removal-probability modelling serve separate purposes.
- **Removal research:** Constructs dated labels, fits a constant-probability baseline and logistic regressions, evaluates chronological holdouts and probability cutoffs, and saves observations, parameters, metrics and source provenance.
- **Data service and explanations:** Exposes membership, prices, features, financial context, rankings and saved results through FastAPI. A separate OpenAI endpoint explains bounded, application-selected evidence with checked numerical references.
- **Reproducible delivery:** Uses SQLAlchemy/Alembic, PostgreSQL, Docker Compose, pytest, Ruff, mypy and GitHub Actions workflow definitions, alongside small, measured performance experiments.

## Data and application flow

```mermaid
flowchart LR
    CSV["Membership CSV<br/>Historical constituents"] --> AUDIT["Audit records and date/identity rules<br/>Retain source hashes"]
    PRICE["Yahoo or CSV prices<br/>Stocks and benchmark"] --> LOAD["Cache, normalize and validate<br/>Batch-load observations"]
    subgraph PG["PostgreSQL evidence"]
        MEMBERS[("Membership sources, identities, intervals<br/>and ADD/REMOVE events")]
        PRICES[("Price sources, normalized<br/>observations and source rows")]
    end
    AUDIT --> MEMBERS
    LOAD --> PRICES
```

Membership and price records are ingested through separate paths into PostgreSQL. Source identities are scoped to membership versions, while price reviews pin permitted mappings, dates and price bases.

```mermaid
flowchart TB
    DB["Dated membership and reviewed price windows"] --> CONTEXT["Features, momentum rankings<br/>and financial context"]
    DB --> COHORT["Monthly active-member cohorts<br/>30-day removal labels"]
    CONTEXT --> COHORT
    COHORT --> FIT["Chronological train/test comparison<br/>Baseline and logistic models"]
    FIT --> RUNS[("Saved reports, predictions<br/>and provenance")]
    DB --> DATAAPI["FastAPI GET<br/>Dated data, features and rankings"]
    CONTEXT --> DATAAPI
    RUNS --> RUNAPI["FastAPI GET<br/>Research runs and records"]
    DB --> EVIDENCE["Application-selected<br/>structured evidence"]
    CONTEXT --> EVIDENCE
    RUNS --> EVIDENCE
    EVIDENCE --> OPENAI["OpenAI structured explanation"]
    OPENAI --> CHECK["Numerical and citation checks"]
    CHECK --> EXPLAIN["FastAPI POST<br/>Explanation and supporting evidence"]
```

Dated database evidence supports descriptive features, removal experiments and API queries. Experiment reports and predictions are saved as versioned files and loaded into a selected API startup snapshot. OpenAI receives application-assembled evidence rather than database credentials or unrestricted query access.

## Run the synthetic demonstration

The local sample is a **self-contained integration example** using invented securities and outcomes to exercise the data pipeline and API. The historical model fit is documented separately. Docker with Compose v2 or later is required. From the repository root:

```sh
export COMPOSE_PROJECT_NAME=indexlens-sample
export INDEXLENS_DB_PORT=55433
export INDEXLENS_API_PORT=18000

docker compose build api
docker compose up -d --wait db
docker compose run --rm migrate
docker compose run --rm --no-deps api python scripts/sample.py
docker compose up -d --wait api
python3 scripts/smoke.py --base-url http://127.0.0.1:18000
```

Try a few read-only requests:

```sh
curl 'http://127.0.0.1:18000/api/v1/indices/sample/securities'
curl 'http://127.0.0.1:18000/api/v1/indices/sample/membership?as_of=2026-05-29'
curl 'http://127.0.0.1:18000/api/v1/rankings?review_id=sample-review&reference_date=2026-05-29&membership=true'
curl 'http://127.0.0.1:18000/api/v1/research/runs/sample-run'
```

Interactive endpoint documentation is available at [`/docs`](http://127.0.0.1:18000/docs). The sample covers the original three-feature research workflow and exercises expected API error paths. With `OPENAI_API_KEY` empty, explanation requests return HTTP `503` / `openai_not_configured`, while data endpoints remain available. A live OpenAI example requires separate configuration.

See [reproducible delivery](docs/delivery.md) for a local Python setup, repeated loading, recovery checks, troubleshooting and safe cleanup of the isolated Compose project.

## Historical removal experiment

**Research question: For a constituent active at a calendar month-end, is there a matching *recorded* removal within the following *30 calendar days*?** Labels use source-record positions and membership-date guards. Incomplete or ambiguous outcomes are excluded; predictors use reviewed price observations dated no later than the reference date.

The chronological design uses training references from **2022–2024** (outcomes ending by 31 December 2024) and a retrospective test period from **January 2025 through July 2026**. A training-prevalence baseline is compared with an original three-feature logistic regression and an expanded nine-feature specification using stock and S&P 500 price-index context. Both logistic models are fitted and evaluated on the **same eligible cohort**, with scaling fitted on training observations only.

| Eligible observations | Training | Test |
|---|---:|---:|
| Rows | 15,289 | 9,252 |
| Distinct removal events | 25 | 19 |
| Source identities | 503 | 513 |

| Test metric | Constant baseline | Three-feature | Nine-feature |
|---|---:|---:|---:|
| Average precision | 0.00205 | 0.0285 | 0.0368 |
| Brier score | 0.00205 | 0.00204 | 0.00203 |

Higher average precision and lower Brier score are preferable. Average precision measures how well recorded removals are ranked across decision cutoffs; the Brier score measures probability error. The expanded model performs better on these two metrics **within this retrospective comparison**, but rare positive events make both measures sensitive to the study design.

At an **0.05 probability cutoff**, the expanded model produced **7 alerts**: 1 true positive, 6 false positives and 18 missed removals (14.29% precision, 5.26% recall). Lower cutoffs catch more removals while generating substantially more false alerts; selecting an operational threshold requires independent validation.

**Interpretation matters more than the apparent metric improvement.** Feature and cutoff choices were revisited after inspecting the test period; observations from the same securities and removal events are dependent; historical mapping, delisted-security coverage, adjustments and point-in-time information availability remain uncertain. These findings remain exploratory; testing predictive usefulness requires evaluating a frozen specification on genuinely unseen data.

The [research methodology and complete cutoff grid](docs/research.md) document construction, exclusions and interpretation. [Machine-readable results](docs/examples/removal-research/metrics.json) preserve full precision, versions and source hashes. Reproducing the fit requires obtaining the corresponding reviewed datasets; the public examples provide saved results and provenance.

## Evidence-backed explanations

The explanation endpoint is a separate step: **IndexLens calculates and selects the evidence; OpenAI generates the narrative.** The application checks cited scalar values and evidence references before rendering the output. These checks compare numerical citations with the supplied evidence; source accuracy and qualitative interpretation require separate review.

Two reviewed examples show the saved output and the material behind it:

- [Research model comparison](docs/examples/removal-research/model-comparison/README.md): baseline, fitted models, operating cutoffs and limitations.
- [Company financial context](docs/examples/removal-research/company-financial-context/README.md): historical stock/benchmark indicators, supplementary context and an attached saved probability.

Their [example index](docs/examples/removal-research/README.md) links to the evidence, structured drafts, rendered explanations and provenance. Verify the public examples offline, after installing IndexLens:

```sh
python docs/examples/removal-research/verify.py
```

Ordinary automated tests mock provider calls. Live requests require a compatible OpenAI model, a configured credential and potentially paid usage. See [explanation design and failure handling](docs/explanations.md).

## Engineering and verification

**Stack:** Python 3.12 · Pandas / NumPy · scikit-learn · PostgreSQL 17 · SQLAlchemy / Alembic · FastAPI · OpenAI Responses API · Docker Compose.

After [installing the constrained development dependencies](docs/delivery.md#configuration-and-local-python-route), configure a PostgreSQL test database and run:

```sh
export INDEXLENS_TEST_DATABASE_URL='postgresql+psycopg://indexlens:indexlens_local@127.0.0.1:55433/indexlens'
OPENAI_API_KEY='' python -m pytest -q
python -m ruff check src/indexlens tests migrations scripts
python -m ruff format --check src/indexlens tests migrations scripts
MYPYPATH=src python -m mypy --explicit-package-bases src/indexlens tests migrations scripts
```

These commands use the sample database port; adjust it to the service actually running. PostgreSQL tests create isolated schemas when the test database URL is configured; otherwise they **skip**. The latest recorded **local** check (26 October 2026) passed 569 tests without skips, lint, formatting, strict type checks and a fresh Docker build/migration/sample/API smoke run. GitHub Actions checks are configured; their remote results still require verification.

The [delivery guide](docs/delivery.md) describes environment isolation, startup checks and CI. [Performance notes](docs/performance.md) document bounded measurements and their trade-offs.

## Documentation map

| Topic | Guide |
|---|---|
| System components and engineering decisions | [Architecture](docs/architecture.md) |
| Membership provenance, dates and CSV audit | [Membership methodology](docs/membership_methodology.md) · [Source audit](docs/data_audit_methodology.md) |
| Database schema, migrations and transactions | [Database](docs/database.md) |
| Provider access, normalization and review | [Price ingestion](docs/prices.md) |
| Numerical definitions and descriptive context | [Features](docs/features.md) |
| Dataset construction, fitting and evaluation | [Research](docs/research.md) |
| API identifiers, requests and error responses | [API](docs/api.md) |
| Explanation checks and evidence contracts | [Explanations](docs/explanations.md) |
| Local deployment, tests and measurements | [Delivery](docs/delivery.md) · [Performance](docs/performance.md) |

## Scope and data limitations

The membership source is a [Wikipedia-derived historical S&P 500 dataset](exploratory_data_audit/exploratory_data_audit.md); market histories come from Yahoo/yfinance, with separate benchmark evidence. Source snapshots and review rules make assumptions inspectable. Official S&P Dow Jones Indices membership decisions and historical point-in-time data availability require independent verification. Price adjustments, corporate actions, unresolved identities, missing acquired/delisted histories and selection effects affect the usable research population.

IndexLens is an independent research application. Its outputs are not official S&P Dow Jones Indices records, independently validated removal forecasts or investment advice.

Copyright © 2026 Maya Hua. All rights reserved.
