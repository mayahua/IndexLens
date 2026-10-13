# PostgreSQL source history and price evidence

The database stores audited membership CSVs, source events, and offline price
evidence. See [price ingestion](prices.md) for migration `0002` and the repeatable
CSV command. Live Yahoo fetching is documented in the price guide.
The existing in-memory membership library remains the semantic reference.

## Local setup

Use Python 3.12+ and a running Docker service. From the repository root:

```sh
python -m pip install -e ".[dev]"
docker compose up -d --wait
export INDEXLENS_DATABASE_URL='postgresql+psycopg://indexlens:indexlens_local@127.0.0.1:55432/indexlens'
python -m alembic upgrade head
```

Compose binds PostgreSQL 17 to localhost port 55432. Its displayed password is
only a local development default. Data lives in the `indexlens_postgres_data`
Docker volume. `docker compose stop` retains it. These database commands read
the exported environment variable; they do not automatically read `.env`.
Change `INDEXLENS_DB_PORT` and the connection URL together if the port is occupied.

## Repeatable sample load

The repository sample is explicitly synthetic, with dates in 2025–2026:

```sh
python -m indexlens.db load tests/fixtures/source_history_synthetic.csv \
  --source synthetic-example --dataset-kind synthetic \
  --coverage-start 2026-01-01 --coverage-end 2026-01-31
```

The JSON result reports the source version ID, row/event counts and file hash.
Repeat the command: the version ID is unchanged and `inserted` becomes `false`.
Use the returned ID for verification, for example:

```sh
python -m indexlens.db verify 1
```

Verification checks the stored original and audited field dictionaries, record
hashes, positions, identity links and version counts/types against the saved CSV
bytes. It compares every stored interval's dates and flags, including intervals
outside coverage or without events, plus all events and source audit issues.
It also compares library and database snapshots at both coverage endpoints and
immediately before/on/after every addition, removal and creation date within
coverage. Row order, values, types, flags, issues and returned metadata must match.

These checks raise errors explicitly and stay active under `python -O`.
The comparison uses the stored CSV as its reference. Source label, real/synthetic
kind and reviewed coverage remain caller declarations; this check does not
independently establish their historical accuracy.

Load an existing real CSV by supplying its path, a stable source label,
`--dataset-kind real` and reviewed coverage boundaries. No sample cap applies.
Coverage is caller-reviewed, never inferred from a successful database write.

## Tables and keys

| Table | One row represents | Key |
|---|---|---|
| `source_versions` | A labelled input file and reviewed coverage | `id`; unique source + file hash + real/synthetic kind |
| `source_identities` | A distinct source `(symbol, cik)` within a version | version + identity position |
| `source_rows` | An original CSV record | version + zero-based record position |
| `membership_intervals` | Parsed membership dates and flags for that record | version + record position |
| `source_events` | A nominal ADD or REMOVE from that interval | version + record position + event type |
| `source_issues` | An ordered source-wide audit finding | version + issue position |

Foreign keys include the version, preventing cross-version evidence links.
Source identities are not verified securities or provider mappings. CIKs remain
text with leading zeros; distinct symbols sharing a CIK remain separate.
Repeated/overlapping intervals, approximate dates and equal endpoints are
retained. They are not uniqueness errors. Equal endpoints never create active
membership, but can contribute both nominal events.

The version stores the exact file bytes, SHA-256, column order and audited frame
types. Each source row stores original field strings and a separate audited
representation, where the existing reader's missing markers become null.
Record SHA-256 hashes canonical JSON of original fields (sorted keys, UTF-8,
compact separators), not physical CSV lines. Exact byte evidence is in the
version. Python-returned frames preserve the CSV reader's index positions;
this adapter accepts CSV files, not arbitrary in-memory DataFrame indices.

## Query and transaction contract

`membership_at_db(session, version_id, as_of)` filters intervals in SQL using
addition <= date, removal > date or null, and creation <= date. Queries outside
inclusive coverage fail. It returns the existing `MembershipSnapshot` shape,
including source issues and dated ambiguity warnings. Returned date columns and
flags come directly from the stored intervals; database reads do not rerun the
source audit. Both query paths use the same active-identity warning helper.
`source_events_db(session, version_id)` reads stored nominal events in
`(coverage_start, coverage_end]`, with complete original evidence. Event order
matches the library using PostgreSQL's C collation for text sorting. Do not
derive guarded membership from nominal events alone.

Repository callers own their SQLAlchemy session. The loader owns one new session
and transaction per operation. A failure rolls back the whole version, including
rows already inserted. A unique version key and PostgreSQL conflict handling
also prevent duplicates when two loaders run concurrently. Different coverage
for an existing version is rejected. No mutable session is shared between tasks.
Treat loaded evidence as immutable; edit the input into a new file version to
make a correction. Existing versions are not silently rewritten.

## Checks

```sh
export INDEXLENS_TEST_DATABASE_URL="$INDEXLENS_DATABASE_URL"
python -m pytest -q
python -m ruff check src/indexlens/data_audit.py src/indexlens/membership.py src/indexlens/db src/indexlens/prices tests migrations
MYPYPATH=src python -m mypy --explicit-package-bases src/indexlens/data_audit.py src/indexlens/membership.py src/indexlens/db src/indexlens/prices tests migrations
```

PostgreSQL integration tests create and remove their own uniquely named schemas.
They do not clear application tables. Without the test URL they are explicitly
skipped; supplying an unreachable URL fails. Ordinary tests make no provider calls.
The included GitHub workflow runs these checks against a PostgreSQL service.

Migrations `0001` (source history) and `0002` (price evidence) are explicit and versioned; upgrades do not call `create_all`.
Tests check migration upgrade/downgrade/re-upgrade, schema/model agreement,
library parity, repeated/concurrent loads, coverage conflicts, source preservation,
foreign keys and an actual constraint failure after earlier writes. Regression
tests deliberately change stored evidence and require verification to fail,
including through the command-line entry point under Python optimization.
A query regression test also checks that stored intervals are returned without
rerunning the audit.

The full CSV is deliberately materialized for this small source-history load.
This is not a streaming or memory-bounded price ingestion pipeline.

Implementation references: [SQLAlchemy transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html),
[Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[official PostgreSQL image](https://hub.docker.com/_/postgres).
