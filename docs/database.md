# PostgreSQL source history and price evidence

The database stores membership CSV files, their validation results, recorded additions and removals, and saved price data. The [price guide](prices.md) describes the price tables added by migration `0002`, loading prices from CSV, and downloading prices from Yahoo. Tests check that database membership queries match the results from [membership.py](../src/indexlens/membership.py). [Features and momentum ranking](features.md) query reviewed price evidence without adding tables or changing applied migrations. [Monthly removal research](research.md) reuses these sources and saves reproducible experiment reports under `data/`. The [public API](api.md) exposes these results through validated read-only requests.

For the complete API/container sample, follow [reproducible delivery](delivery.md).

## Local setup

Use Python 3.12+ and start Docker. Run these commands from the repository root:

```sh
python -m pip install -e ".[dev]"
docker compose up -d --wait db
export INDEXLENS_DATABASE_URL='postgresql+psycopg://indexlens:indexlens_local@127.0.0.1:55432/indexlens'
python -m alembic upgrade head
```

The final command applies migrations: numbered instructions that create or update the database tables. On first setup, these tables contain no membership or price records; load those separately. Later runs apply only migrations that have not already been applied.

Compose runs PostgreSQL 17 at `127.0.0.1` (your computer) on port 55432. The database password ('indexlens_local') shown above is only a local development default. Data is saved in `indexlens_postgres_data`, a storage area managed by Docker. `docker compose stop` retains it. These database commands read the exported environment variable; they do not automatically read `.env`. If port 55432 is occupied, export `INDEXLENS_DB_PORT` with a free port number before starting Compose, and change the port in `INDEXLENS_DATABASE_URL` to match.

## Load and check the sample CSV

```sh
python -m indexlens.db load tests/fixtures/source_history_synthetic.csv \
  --source synthetic-example --dataset-kind synthetic \
  --coverage-start 2026-01-01 --coverage-end 2026-01-31
```

The repository sample is explicitly synthetic, with dates in 2025–2026. The coverage start and end dates declare the period supported for membership queries, including both endpoints. The JSON result reports the source version ID, row and event counts and a SHA-256 hash, a value calculated from the file contents to detect changes.

Repeating the same load keeps the version ID and returns `inserted: false`, meaning no new copy was added. Use the returned ID to verify the saved data; replace `1` below with the actual ID:

```sh
python -m indexlens.db verify 1
```

Verification compares the saved database records with the saved CSV file. It checks original and validated values, record hashes, positions, links to identities, record counts and data types. It compares every membership interval's dates and flags, including intervals outside coverage or without events, plus all events and file validation findings. It also compares Python and database membership results at both coverage endpoints and around every addition, removal and creation date. These comparisons cover each date and the day before and after it, wherever they fall within coverage. Row order, values, types, flags, issues and returned metadata must match. The checks report errors even with Python’s `-O` (optimization) option, which disables some built-in debugging checks.

The comparison uses the stored CSV as its reference. The source label, real/synthetic type and coverage dates are supplied when loading the file; verification does not independently confirm their historical accuracy. Load an existing real CSV by supplying its path, a stable source label, `--dataset-kind real` and reviewed coverage date boundaries. The loader processes all records in the supplied CSV file. A successful load does not prove that the file supports membership queries throughout the declared coverage.

## Database tables

A version identifies one saved source file. These tables store membership history; see the [price guide](prices.md#tables-and-validation) for price tables. Here, `+` means the listed values together identify one row. Positions are counted from zero.

| Table | Each row represents | Row identifier |
|---|---|---|
| `source_versions` | A labelled input file and its supported query coverage | `id`; the source label + file hash + real/synthetic type |
| `source_identities` | A distinct source `(symbol, cik)` pair in a version | version + identity position |
| `source_rows` | An original CSV record | version + record position |
| `membership_intervals` | Parsed membership dates and flags for that record | version + record position |
| `source_events` | An ADD or REMOVE recorded from those dates | version + record position + event type |
| `source_issues` | A file validation finding, kept in its original order | version + issue position |

Database rules require linked records to belong to the same file version. A stored symbol/CIK pair does not prove which security it represents or which provider symbol matches it. CIK identifiers remain text with leading zeros; different symbols with the same CIK remain separate identities. Repeated/overlapping intervals, approximate dates, and intervals with equal endpoints (start date = end date) are retained. They are not uniqueness errors. Intervals with equal endpoints don't count as active membership, but can contribute both ADD and REMOVE events. Validation rejects exact duplicate CSV rows.

Each version stores an unchanged copy of the original CSV, its SHA-256 hash, column order and validated data types. Each row keeps both its original field strings and the values produced by validation. Recognised missing values are stored as `null` in the validated copy. A row’s SHA-256 hash is calculated from its original fields written as JSON with sorted field names, UTF-8 encoding and no extra spacing. It is not calculated from the CSV line’s formatting. The database loader accepts CSV files. Returned membership rows and event source records preserve the original CSV row positions. The event table has its own row numbering and retains the CSV position in `source_record_position`.

## Read membership and save data

The query functions below are defined in [repository.py](../src/indexlens/db/repository.py).

`membership_at_db(session, version_id, as_of)` returns membership on the requested date. A membership period qualifies when:

- The addition date is on or before that date.

- The removal date is after that date, or is missing.

- The record’s creation date (`created_at`) is on or before that date.

The requested date must fall within the saved coverage range, including its start and end dates. Queries outside the coverage range fail. The result uses the existing `MembershipSnapshot` format, including file validation findings and warnings about uncertain membership on the requested date. Returned date columns and flags come directly from saved records; reading the database does not validate the file again. Both the original Python code and the database query use the same function to warn about repeated active symbol/CIK pairs or a symbol with multiple active CIKs.

`source_events_db(session, version_id)` returns recorded ADD and REMOVE events **after** the coverage start date and **on or before** the coverage end date, along with the full validated source table, including records without events. Events keep their recorded dates and approximate-date flags. If a record’s `created_at` is later than an event date, `created_after_event` is true; the event is retained. This flag does not prove when the information became publicly available. Events are sorted by date, symbol, CIK, event type and source row position. Text fields use PostgreSQL’s `C` sorting rule to match the Python code. The order of events on the same date does not establish which happened first. Recorded events alone do not establish active membership; use the membership query to apply all the date rules and receive its warnings.

Code calling these query functions opens and closes its own SQLAlchemy database session, which tracks database operations. The loader opens a new session for each load and saves the whole file as one operation. If a load fails, none of its changes are kept, including rows already inserted. Database rules prevent duplicate versions even when two loads run at the same time. Loading an existing version with different coverage dates is rejected. Sessions must not be shared between tasks. To correct source data, edit the CSV and load it as a new version. Existing versions are preserved.

## Checks

```sh
export INDEXLENS_TEST_DATABASE_URL="$INDEXLENS_DATABASE_URL"
python -m pytest -q
python -m ruff check src/indexlens/data_audit.py src/indexlens/membership.py src/indexlens/db src/indexlens/prices src/indexlens/features src/indexlens/research src/indexlens/api tests migrations
MYPYPATH=src python -m mypy --explicit-package-bases src/indexlens/data_audit.py src/indexlens/membership.py src/indexlens/db src/indexlens/prices src/indexlens/features src/indexlens/research src/indexlens/api tests migrations
```

Tests that use PostgreSQL create and remove their own uniquely named schemas: separate groups of tables within the database. They do not clear application tables.

If `INDEXLENS_TEST_DATABASE_URL` is unset, these tests are reported as skipped. If it points to a database that cannot be reached, the tests fail. Ordinary tests do not make data provider calls. The included GitHub workflow runs these checks against a PostgreSQL service.

Database table changes are defined in numbered migration files: `0001` for source history and `0002` for price data. Upgrades apply these files instead of using SQLAlchemy’s `create_all` to create tables directly from Python definitions.

Tests check that migrations can be applied, reversed and applied again; that database tables match their Python definitions; and that database queries match the original membership code. They also check repeated and simultaneous loads, rejection of conflicting coverage dates, preservation of source data, valid links between records and complete removal of a failed load’s changes. Verification detects deliberately altered stored data, including when run from the command line with Python’s `-O` option. A test also checks that queries use stored intervals without validating the file again.

The membership-history loader reads the entire CSV file into memory, so larger files require more memory. The database-only command above starts PostgreSQL. The complete delivery workflow runs migrations, sample loading and API startup as separate steps.

Implementation references: [SQLAlchemy transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html), [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html), [official PostgreSQL image](https://hub.docker.com/_/postgres).
