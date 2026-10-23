"""Fetch the five-symbol Yahoo demonstration or every identity in a manifest."""

import argparse
import asyncio
import json
import sys
from datetime import date
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from indexlens.db.models import PriceDataset, PriceSource
from indexlens.db.session import database_engine

from .ingestion import Options, ingest_manifest, validate_manifest
from .yahoo import BENCHMARKS

DEMO_SYMBOLS = ("MSFT", "JPM", "XOM", "JNJ", "CAT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    for name in ("demo", "research", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("--dataset-id", required=True)
        command.add_argument("--cache", type=Path, default=Path("data/cache/yahoo"))
        command.add_argument("--concurrency", type=int, default=2)
        command.add_argument("--request-interval", type=float, default=1.0)
        command.add_argument("--request-timeout", type=float, default=15.0)
        command.add_argument("--job-timeout", type=float, default=90.0)
        command.add_argument("--retries", type=int, default=2)
        command.add_argument("--backoff", type=float, default=2.0)
        command.add_argument("--batch-size", type=int, default=500)
        command.add_argument("--offline", action="store_true")
        command.add_argument("--retry-failed", action="store_true")
        if name != "research":
            command.add_argument("--start", type=date.fromisoformat, required=True)
            command.add_argument("--end", type=date.fromisoformat, required=True)
            if name == "benchmark":
                command.add_argument("--symbol", choices=BENCHMARKS, default="^GSPC")
        else:
            command.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "research":
            manifest = json.loads(args.manifest.read_bytes())
        else:
            symbols = (args.symbol,) if args.mode == "benchmark" else DEMO_SYMBOLS
            manifest = dict(
                version="yahoo-benchmark-v1"
                if args.mode == "benchmark"
                else "yahoo-five-symbol-demo-v1",
                data_kind="real",
                source_sha256=sha256("\n".join(symbols).encode()).hexdigest(),
                source_note="Declared price demo universe; no security join approved.",
                eligible_source_identity_count=len(symbols),
                identities=[
                    dict(
                        symbol=s,
                        cik="",
                        provider_mapping_status="pending_historical_review",
                    )
                    | (
                        dict(instrument_type=BENCHMARKS[s])
                        if args.mode == "benchmark"
                        else {}
                    )
                    for s in symbols
                ],
                price_request_envelope=dict(
                    start_inclusive=args.start.isoformat(),
                    end_exclusive=args.end.isoformat(),
                ),
            )
        options = Options(
            **{name: getattr(args, name) for name in Options.__dataclass_fields__}
        )
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object.")
        validate_manifest(manifest)
        options.validate()
        engine = database_engine()
        try:
            with engine.connect() as connection:
                connection.execute(select(PriceSource.id).limit(0))
                kind = connection.scalar(
                    select(PriceDataset.data_kind).where(
                        PriceDataset.dataset_id == args.dataset_id
                    )
                )
                if kind is not None and kind != "real":
                    raise ValueError("Dataset is already synthetic.")
            result = asyncio.run(
                ingest_manifest(engine, manifest, args.dataset_id, args.cache, options)
            )
        finally:
            engine.dispose()
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "status",
                        "requested_identities",
                        "loaded",
                        "failed",
                        "report_path",
                    )
                }
            )
        )
        return 2 if result["failed"] else 0
    except KeyboardInterrupt:
        print(
            "Cancelled; completed sources and cache evidence remain resumable.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, KeyError, TypeError, SQLAlchemyError):
        print(
            "Ingestion failed; check configuration, manifest and cache evidence.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
