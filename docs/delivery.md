# Reproducible local delivery

The public synthetic workflow loads invented membership and prices, calculates
short-window features and synthetic removal research results, and serves them
through the API.
The sample requires Docker with Compose v2 or later. Run commands from the repository root.
Installation downloads public images and packages; sample execution uses local
fixtures and PostgreSQL. No private configuration, market download or OpenAI key is
required.

## Start a separate sample stack

Choose an unused project name and free localhost ports. A different Compose project
gets separate database and sample volumes; keep the same exports for every command:

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

The image runs as UID 10001. Its build context allows only application source,
migrations, scripts and dependency files. Private files, `.env`, Git metadata and
datasets never enter the context. The named sample volume holds generated inputs,
review, saved run and API configuration under `/app/data/sample`.

PostgreSQL readiness gates migration startup; migration success gates API startup.
The API health check reads required tables. These conditions follow
[Compose dependency readiness](https://docs.docker.com/compose/how-tos/startup-order/).
Missing publication files stop API startup. Load the sample before starting it.
The API snapshots configuration at startup, so restart it after changing publication
files. PostgreSQL health alone does not establish migration or application readiness.

`migrate` runs the existing `python -m alembic upgrade head`. The sample helper
prints results from `python -m indexlens.db load`, `indexlens.db verify`,
`indexlens.prices` and `indexlens.research`; these remain the application entry points.
The two applied migrations are unchanged. No load runs automatically on API startup.

## Sample contents and repeatability

`scripts/sample.py` generates four membership intervals across three invented
identities and 524 price rows across A and B. C has an approximate addition date and
no approved price mapping; exclusions remain visible. Dates cover 2025–2026, with
declared membership outcomes through 30 September 2026. Prices use invented weekdays,
not a claim about exchange sessions. The review approves only these synthetic inputs.

Training and testing each retain four eligible observations and one removal. Seed 42,
fixture bytes, hashes, feature definitions and inputs are deterministic. Research uses
the checked-out classification rule and records its actual threshold. Compare saved
observations, counts and evaluation values; creation timestamps, output paths, local
database IDs and code provenance may differ. Floating-point results can vary across
architectures and numerical libraries. Synthetic results do not establish predictive
validity. Momentum ranks describe past returns, not removal probabilities.

The helper requires a new output directory and preserves existing runs. To repeat the
load in the same database and retain a second report:

```sh
docker compose run --rm --no-deps api python scripts/sample.py --output data/repeat
```

Membership and price loaders report `inserted: false` for identical evidence. Their
version/source IDs remain stable. The API continues serving the first run. To publish
the second, export `INDEXLENS_CONTAINER_API_CONFIG=/app/data/repeat/api.json` and run
`docker compose up -d --wait api` again.

## Requests and failures

```sh
curl 'http://127.0.0.1:18000/api/v1/indices'
curl 'http://127.0.0.1:18000/api/v1/indices/sample/securities'
curl 'http://127.0.0.1:18000/api/v1/indices/sample/membership?as_of=2026-05-29'
curl 'http://127.0.0.1:18000/api/v1/rankings?review_id=sample-review&reference_date=2026-05-29&membership=true'
curl 'http://127.0.0.1:18000/api/v1/research/runs/sample-run'
curl -i 'http://127.0.0.1:18000/api/v1/research/explain' \
  -H 'Content-Type: application/json' \
  -d '{"target":{"kind":"run","run_id":"sample-run"}}'
```

Compose explicitly supplies an empty OpenAI key, including when the host has one.
The last request returns `503 openai_not_configured`; data requests remain available.
`scripts/smoke.py` exercises the original thirteen GET routes, valid empty results, invalid dates/ranges and unknown
IDs, an unsupported question, and the missing-key response.
Live OpenAI configuration is a separate workflow in [explanations.md](explanations.md).

For an isolated outage check, stop this sample project's `db`, request `/api/v1/health`
(503), and request `/api/v1/research/runs/sample-run` (200, saved startup snapshot).
Start `db` with `docker compose up -d --wait db` and check health again. Unhealthy
containers are reported by Docker; this local setup does not automatically restart
services on health failure. A failed migration prevents dependent API startup.

`docker compose stop` retains both volumes. `docker compose down` removes this
project's containers/network and retains data. Only for a disposable sample project,
`docker compose down --volumes` also deletes its generated data. Verify the project
name before cleanup; existing `indexlens_postgres_data` belongs to the default project.

## Configuration and local Python route

| Setting | Default | Meaning |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `indexlens` | Names isolated resources and application image |
| `INDEXLENS_DB_PORT` | `55432` | PostgreSQL localhost port |
| `INDEXLENS_API_PORT` | `8000` | API localhost port |
| `INDEXLENS_CONTAINER_API_CONFIG` | `/app/data/sample/api.json` | Publication file inside the sample volume |

The local password `indexlens_local` is a development default. Both host ports bind
to localhost. This is local delivery; authentication and remote hosting require
separate configuration. Compose may read `.env` for substitution; Python commands
read exported variables. No `.env` file is required for the documented sample.

With Python 3.12 in a fresh virtual environment:

```sh
python -m pip install -c requirements.lock setuptools wheel
python -m pip install --no-build-isolation -c requirements.lock -e '.[dev]'
python -m pip check
export INDEXLENS_DATABASE_URL='postgresql+psycopg://indexlens:indexlens_local@127.0.0.1:55433/indexlens?connect_timeout=5'
python -m alembic upgrade head
python scripts/sample.py
export INDEXLENS_API_CONFIG=data/sample/api.json
python -m uvicorn indexlens.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Start the selected Compose `db` first. Local sample files live in this checkout's
`data/`, independently of the container sample volume. The requirements constraints
pin runtime and development versions for Python 3.12; `pyproject.toml` remains the
dependency contract. The Docker Python base is pinned by digest. PostgreSQL follows
the 17 tag, so record its resolved digest/version when comparing runs. Package pins
are version constraints, not artifact hashes or a promise of bit-identical images.
Update pins deliberately and rerun quality/container checks. See Docker's
[build guidance](https://docs.docker.com/build/building/best-practices/).

An unpublished source snapshot must include the working versions of tracked files
and the new public `Dockerfile`, `.dockerignore`, `requirements.lock`, `scripts/`,
tests and documentation. `git archive HEAD` omits uncommitted work. Exclude private
`mydocs/`, `.env` and `data/`; preserve a file-hash manifest beside the snapshot.
Saved runs from a snapshot/image record null Git revision/dirty fields and actual
source file hashes; they must not be described as a clean commit.

## Checks and logs

```sh
export INDEXLENS_TEST_DATABASE_URL="$INDEXLENS_DATABASE_URL"
python -m ruff check src/indexlens tests migrations scripts
MYPYPATH=src python -m mypy --explicit-package-bases src/indexlens tests migrations scripts
OPENAI_API_KEY='' python -m pytest -q
```

PostgreSQL tests use unique temporary schemas and remove only their own schemas.
Unset test URLs produce skipped database tests, not a successful integration gate.
GitHub Actions defines the same checks with PostgreSQL plus a separate image-build,
migration/sample/startup and HTTP smoke job. Ordinary CI uses mocked providers and
an empty OpenAI key. A local run verifies local behaviour; only an observed GitHub
Actions result establishes remote CI success.

Ingestion commands print source hashes, insertion status and counts; Yahoo ingestion
also prints status/error categories. Research command summaries include research,
feature/model versions and Git revision/dirty state; full provenance stays in the
saved report. API logs contain method, route template, status and elapsed milliseconds
to response headers. They omit raw paths, query strings, headers, bodies and exception
details. Unexpected request failures return a generic 500. Use `--no-access-log` with
Uvicorn to avoid its separate raw-URL access log, and keep SDK/SQL debug logs disabled.

Run the [bounded performance comparisons](performance.md) against a disposable database.
