# Bounded performance comparisons

Run from the repository root after the [sample stack](delivery.md) is available,
keeping its Compose project/port exports:

```sh
mkdir -p data
docker compose run --rm --no-deps api python scripts/benchmark.py --repeats 5 > data/performance.json
```

Alternatively, export `INDEXLENS_DATABASE_URL` for a disposable PostgreSQL database
and run `python scripts/benchmark.py --repeats 5`. The database comparison creates
only a connection-local temporary table, which disappears when the connection ends.
It does not alter application data, indexes or migrations.

The following measurements were recorded on 21 October 2026 in the application
container: Python 3.12.15, Linux 6.12.76/aarch64 (Docker Desktop VM), PostgreSQL 17.11.
Each variant has one warmup and five timed runs using `perf_counter`; the table shows
the median and observed range. A separate run uses `tracemalloc` for peak Python
allocations, so allocation tracing does not distort the reported timings. All
comparisons check equivalent outputs and fail if they differ. No performance threshold
is imposed in CI.

| Comparison / variant | Median milliseconds | Observed range, ms | Peak traced Python bytes |
|---|---:|---:|---:|
| Identity: list lookup | 82.844 | 82.479–85.853 | 12,776 |
| Identity: construct set and lookup | 0.200 | 0.196–0.229 | 164,104 |
| Materialize then batch | 15.371 | 15.210–15.822 | 22,406,800 |
| Generate then batch | 11.180 | 11.118–12.226 | 234,720 |
| Database: per-row execution | 162.087 | 159.644–162.624 | 194,161 |
| Database: batches of 500 | 17.183 | 17.004–18.739 | 275,790 |

## Identity-set comparison

Inputs are 3,000 `(symbol, cik)` tuples and 3,000 candidate tuples; 1,500 match.
Symbols repeat across different CIKs, so identity comparison retains both fields.
Both variants produce the exact same ordered matching list. Input construction is
outside measurement; building the lookup set is inside measurement.

List membership scans up to the whole population for each candidate: O(N × M) work.
A set needs O(N) additional storage and expected O(N + M) construction/lookups.
Both also allocate the O(K) output. The measured peak includes that output and the
temporary set, but excludes the pre-existing input tuples. Hash lookup performance
depends on the keys and collision behaviour.

Chosen tradeoff: retain identity sets for repeated membership checks where the
population fits memory; retain ordered inputs for deterministic output. A single
lookup in a tiny list may not justify building a set. The demonstration does not
measure the complete membership workflow or authorize merging identities by ticker.

## Batching and materialization

Both variants generate 100,003 two-field dictionaries, use the application's `batches`
iterator with batch size 500, and produce `(count, value sum) = (100003, 4799958)`.
The last batch contains three rows. One variant first builds a complete list; the
other feeds the generator directly to the batch iterator. Record generation,
batching and aggregation are timed in both variants.

Materialization retains O(N) dictionaries plus O(B) batch references. Direct generation
retains O(B) live dictionaries; the transition between batches can briefly retain
both old and new batches. The scalar aggregate needs O(1) space. This explains the
measured allocation difference; batching an already built list does not remove it.

Chosen tradeoff: retain the real iterator and bounded row conversion. The current
price loader deliberately preserves complete input bytes and decoded CSV evidence,
so its overall memory is still O(input size), plus batch/session state. This benchmark
isolates record production; it is not a measurement of end-to-end ingestion and does
not claim the existing loader is fully streaming. Changing evidence storage would
require a separate design decision.

## Database bulk-loading comparison

A temporary table has an integer primary key and a required integer value. Both
variants insert the same prebuilt 2,000 dictionaries and read back all 2,000 ordered
pairs for exact equality. One makes 2,000 SQLAlchemy execute calls; the other makes
four executemany calls using batches of 500. Each trial includes table truncation,
insertion and the same ordered read. Table/input creation is outside timing; trials
share one connection and transaction.

Batching reduces client execution overhead and lets the driver handle groups of
parameters. The shared input and readback are O(N); batched parameter preparation
adds O(B) temporary allocations. Python tracing excludes PostgreSQL memory, network
buffers and untraced native allocations. These numbers are not database-server
memory measurements.

Chosen tradeoff: retain the membership loader's existing bulk inserts. Keep price
loading's per-record conflict/provenance checks, whose semantics are more complex
than this table. A faster temporary-table insert does not justify bypassing those
checks. No new index or migration is needed for this comparison.

## Measurement limits

These are small synthetic demonstrations, not production speedup claims. The VM,
CPU scheduling, Python allocator, cache state and driver/network path influence
timings. Variants run in a fixed order after warmup; there is no cold-cache control
or statistical confidence interval. Database trials omit durable commit cost, real
table/index volume, concurrency, foreign keys and application validation. Peak traced
allocations are auxiliary Python allocations for the measured operation, not process
RSS. Reproduce the method and inspect the output equivalence before interpreting a
timing difference; preserve versions and raw samples when comparing environments.
