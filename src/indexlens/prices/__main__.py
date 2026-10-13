"""Load a saved daily-price CSV and its explicit provenance manifest."""

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from indexlens.db.session import database_engine

from .loading import load_price_csv
from .metadata import PriceMetadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.metadata.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Metadata must be an object.")
        metadata = PriceMetadata.from_dict(raw)
        engine = database_engine()
        try:
            result = load_price_csv(
                engine, args.path, metadata, batch_size=args.batch_size
            )
        finally:
            engine.dispose()
        print(json.dumps(asdict(result)))
    except SQLAlchemyError:
        print(
            "Price load failed; check database connectivity and migrations.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, TypeError, csv.Error):
        print(
            "Price load failed; check CSV, manifest and dataset declarations.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
