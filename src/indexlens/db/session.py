"""Engine configuration; callers own a separate session per operation."""

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url


def database_engine(url: str | None = None) -> Engine:
    value = url or os.environ.get("INDEXLENS_DATABASE_URL")
    if not value:
        raise ValueError("Set INDEXLENS_DATABASE_URL to a PostgreSQL connection URL.")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("Use a postgresql+psycopg database URL.")
    return create_engine(parsed, pool_pre_ping=True, hide_parameters=True)
