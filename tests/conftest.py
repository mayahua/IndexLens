"""Shared PostgreSQL schema isolation and migration helpers for tests."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from indexlens.db.session import database_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database() -> Iterator[Engine]:
    url = os.environ.get("INDEXLENS_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set INDEXLENS_TEST_DATABASE_URL to run PostgreSQL integration tests."
        )
    admin = database_engine(url)
    schema = "indexlens_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = database_engine(scoped_url.render_as_string(hide_password=False))
    try:
        migrate(engine, "upgrade", "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def migrate(engine: Engine, direction: str, revision: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if direction == "upgrade":
            command.upgrade(config, revision)
        else:
            command.downgrade(config, revision)
