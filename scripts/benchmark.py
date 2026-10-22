"""Measure bounded synthetic identity, batching and PostgreSQL loading choices."""

import argparse
import gc
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy import text

from indexlens.db.session import database_engine
from indexlens.prices.normalization import batches


def measure(operation: Callable[[], Any], repeats: int) -> dict[str, Any]:
    expected = operation()
    times = []
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        actual = operation()
        times.append(time.perf_counter() - started)
        if actual != expected:
            raise ValueError("Outputs differ between repeats.")
    gc.collect()
    tracemalloc.start()
    actual = operation()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if actual != expected:
        raise ValueError("Memory-run output differs.")
    return dict(
        seconds=times,
        median_seconds=statistics.median(times),
        python_peak_bytes=peak,
        output=expected,
    )


def compare(variants: dict[str, Callable[[], Any]], repeats: int) -> dict[str, Any]:
    result = {name: measure(operation, repeats) for name, operation in variants.items()}
    values = list(result.values())
    if any(item["output"] != values[0]["output"] for item in values):
        raise ValueError("Comparison outputs differ.")
    return dict(equivalent_outputs=True, variants=result)


def run(repeats: int) -> dict[str, Any]:
    # Ordered tuples preserve two identities that happen to share a symbol.
    population = [(f"S{i % 500}", f"{i:08}") for i in range(3000)]
    candidates = [(f"S{i % 500}", f"{i:08}") for i in range(1500, 4500)]

    def identity_set() -> list[tuple[str, str]]:
        lookup = set(population)
        return [identity for identity in candidates if identity in lookup]

    identities = compare(
        dict(
            list_membership=lambda: [i for i in candidates if i in population],
            set_membership=identity_set,
        ),
        repeats,
    )
    for result in identities["variants"].values():
        result["output_count"] = len(result.pop("output"))
    identities["inputs"] = dict(population=3000, candidates=3000, matched=1500)

    def records() -> Iterator[dict[str, int]]:
        for i in range(100_003):
            yield dict(position=i, value=i % 97)

    def aggregate(materialize: bool) -> tuple[int, int]:
        rows = list(records()) if materialize else records()
        count, total = 0, 0
        for batch in batches(rows, 500):
            count += len(batch)
            total += sum(row["value"] for row in batch)
        return count, total

    batching = compare(
        dict(
            materialize_then_batch=lambda: aggregate(True),
            generate_then_batch=lambda: aggregate(False),
        ),
        repeats,
    )
    batching["inputs"] = dict(rows=100_003, batch_size=500, final_batch=3)

    engine = database_engine()
    try:
        with engine.connect() as connection, connection.begin():
            # Session-local table; application tables and migrations are untouched.
            connection.execute(
                text(
                    "CREATE TEMP TABLE indexlens_benchmark ("
                    "position integer PRIMARY KEY, value integer NOT NULL) "
                    "ON COMMIT DROP"
                )
            )
            rows = [dict(position=i, value=i % 97) for i in range(2000)]
            statement = text(
                "INSERT INTO indexlens_benchmark (position, value) "
                "VALUES (:position, :value)"
            )

            def insert(bulk: bool) -> list[tuple[int, int]]:
                connection.execute(text("TRUNCATE indexlens_benchmark"))
                if bulk:
                    for batch in batches(rows, 500):
                        connection.execute(statement, batch)
                else:
                    for row in rows:
                        connection.execute(statement, row)
                return [
                    tuple(row)
                    for row in connection.execute(
                        text(
                            "SELECT position, value FROM indexlens_benchmark "
                            "ORDER BY position"
                        )
                    ).tuples()
                ]

            database = compare(
                dict(
                    per_row=lambda: insert(False),
                    bulk_500=lambda: insert(True),
                ),
                repeats,
            )
            for result in database["variants"].values():
                result["output_count"] = len(result.pop("output"))
            database["inputs"] = dict(rows=2000, batch_size=500)
            database["postgres_version"] = connection.scalar(
                text("SHOW server_version")
            )
    finally:
        engine.dispose()
    return dict(
        python=platform.python_version(),
        platform=platform.platform(),
        repeats=repeats,
        timing="perf_counter; one warmup, repeated timings, separate tracemalloc run",
        identity=identities,
        batching=batching,
        database=database,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5, choices=range(3, 11))
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.repeats), indent=2))
