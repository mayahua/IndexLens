"""Query reviewed features and rank securities by 20-session return."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from indexlens.db.session import database_engine

from .query import query_features
from .review import Review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--membership", action="store_true")
    args = parser.parse_args(argv)
    try:
        review = Review.load(args.review)
        engine = database_engine()
        try:
            with (
                engine.connect().execution_options(
                    isolation_level="REPEATABLE READ", postgresql_readonly=True
                ) as connection,
                Session(connection) as session,
                session.begin(),
            ):
                result = query_features(
                    session, review, args.date, membership=args.membership
                )
        finally:
            engine.dispose()
        print(json.dumps(result, allow_nan=False, indent=2))
        return 0 if result["ranking_complete"] else 2
    except (OSError, ValueError, TypeError, KeyError, SQLAlchemyError):
        print(
            "Feature query failed; check review, database, source identity and "
            "membership date coverage.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
