"""Load or verify local membership history in a migrated PostgreSQL database."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from .loading import load_source_csv
from .session import database_engine
from .verification import verify_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load")
    load.add_argument("path", type=Path)
    load.add_argument("--source", required=True)
    load.add_argument("--dataset-kind", choices=["real", "synthetic"], required=True)
    load.add_argument("--coverage-start", type=date.fromisoformat, required=True)
    load.add_argument("--coverage-end", type=date.fromisoformat, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("version_id", type=int)
    args = parser.parse_args(argv)
    try:
        engine = database_engine()
        try:
            if args.command == "load":
                result = load_source_csv(
                    engine,
                    args.path,
                    source=args.source,
                    dataset_kind=args.dataset_kind,
                    coverage_start=args.coverage_start,
                    coverage_end=args.coverage_end,
                )
                print(json.dumps(asdict(result)))
            else:
                count = verify_source(engine, args.version_id)
                print(
                    json.dumps(
                        {
                            "version_id": args.version_id,
                            "snapshot_dates_checked": count,
                            "parity": "passed",
                        }
                    )
                )
        finally:
            engine.dispose()
    except SQLAlchemyError:
        print(
            "Database operation failed; check connectivity and migrations.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, AssertionError) as exc:
        print(
            f"Operation failed ({type(exc).__name__}); check source and coverage.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
