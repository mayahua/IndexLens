"""Tests for database migrations, loading, membership queries, and verification."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from indexlens.db.loading import LoadResult, load_source_csv
from indexlens.db.models import Base, SourceIdentity, SourceRow, SourceVersion
from indexlens.db.repository import membership_at_db, source_events_db
from indexlens.db.session import database_engine
from indexlens.db.verification import verify_source

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "source_history_synthetic.csv"
START = date(2026, 1, 1)
END = date(2026, 1, 31)
pytestmark = pytest.mark.postgres


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


def load(
    engine: Engine, path: Path = FIXTURE, source: str = "synthetic-test"
) -> LoadResult:
    return load_source_csv(
        engine,
        path,
        source=source,
        dataset_kind="synthetic",
        coverage_start=START,
        coverage_end=END,
    )


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table.name: connection.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
            for table in Base.metadata.sorted_tables
        }


def test_migration_matches_models_and_reapplies(database: Engine) -> None:
    migrate(database, "upgrade", "head")
    with database.connect() as connection:
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )
    migrate(database, "downgrade", "base")
    migrate(database, "upgrade", "head")
    assert not any(counts(database).values())


def test_membership_events_evidence_and_warnings_match_library(
    database: Engine,
) -> None:
    result = load(database)
    assert verify_source(database, result.version_id) >= 15
    with Session(database) as session:
        snapshot = membership_at_db(session, result.version_id, date(2026, 1, 6))
        assert {"duplicate_active_identity", "multiple_active_ciks"} <= {
            issue.issue_type for issue in snapshot.issues
        }
        assert "BETA" not in snapshot.data.symbol.tolist()
        assert "ZERO" not in snapshot.data.symbol.tolist()
        assert (
            "BETA"
            in membership_at_db(
                session, result.version_id, date(2026, 1, 7)
            ).data.symbol.tolist()
        )
        assert (
            "BETA"
            not in membership_at_db(
                session, result.version_id, date(2026, 1, 10)
            ).data.symbol.tolist()
        )
        assert (
            "BETA"
            in membership_at_db(
                session, result.version_id, date(2026, 1, 20)
            ).data.symbol.tolist()
        )
        assert session.scalar(select(func.count()).select_from(SourceIdentity)) == 8
        version = session.get(SourceVersion, result.version_id)
        assert version is not None
        assert version.source_bytes == FIXTURE.read_bytes()
        assert version.dataset_sha256 == sha256(FIXTURE.read_bytes()).hexdigest()
        first = session.get(SourceRow, (result.version_id, 0))
        assert first is not None
        assert first.original_fields["date_removed"] == ""
        assert first.audited_fields["date_removed"] is None


def test_repeat_and_concurrent_loads_do_not_duplicate(database: Engine) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: load(database), range(2)))
    assert results[0].version_id == results[1].version_id
    assert sum(result.inserted for result in results) == 1
    before = counts(database)
    assert not load(database).inserted
    assert counts(database) == before
    assert before["source_versions"] == 1
    assert before["source_rows"] == 10


def test_failure_after_row_inserts_rolls_back_whole_load(database: Engine) -> None:
    with database.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE source_events ADD CONSTRAINT test_reject_remove "
                "CHECK (event_type <> 'REMOVE')"
            )
        )
    with pytest.raises(IntegrityError):
        load(database)
    assert not any(counts(database).values())
    with database.begin() as connection:
        connection.execute(
            text("ALTER TABLE source_events DROP CONSTRAINT test_reject_remove")
        )
    assert load(database).inserted


def test_conflicting_coverage_is_rejected(database: Engine) -> None:
    load(database)
    before = counts(database)
    with pytest.raises(ValueError, match="different coverage"):
        load_source_csv(
            database,
            FIXTURE,
            source="synthetic-test",
            dataset_kind="synthetic",
            coverage_start=START,
            coverage_end=date(2026, 2, 1),
        )
    assert counts(database) == before


def test_real_and_synthetic_contexts_remain_separate(database: Engine) -> None:
    synthetic = load(database)
    # The real label here tests namespace isolation only; the fixture is synthetic.
    separate = load_source_csv(
        database,
        FIXTURE,
        source="synthetic-test",
        dataset_kind="real",
        coverage_start=START,
        coverage_end=END,
    )
    assert synthetic.version_id != separate.version_id
    assert counts(database)["source_versions"] == 2
    assert verify_source(database, separate.version_id) >= 15


@pytest.mark.parametrize(
    "coverage_start,coverage_end",
    [
        (date(2025, 11, 1), date(2025, 11, 30)),
        (START, START),
    ],
)
def test_empty_events_and_single_day_coverage(
    database: Engine, coverage_start: date, coverage_end: date
) -> None:
    result = load_source_csv(
        database,
        FIXTURE,
        source="no-events",
        dataset_kind="synthetic",
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    with Session(database) as session:
        events = source_events_db(session, result.version_id)
        assert events.data.empty
        assert len(events.source_data) == 10
    assert verify_source(database, result.version_id) >= 1


def test_unknown_version_and_out_of_coverage(database: Engine) -> None:
    result = load(database)
    with Session(database) as session:
        with pytest.raises(ValueError, match="Unknown"):
            source_events_db(session, result.version_id + 100)
        for day in (date(2025, 12, 31), date(2026, 2, 1)):
            with pytest.raises(ValueError, match="outside"):
                membership_at_db(session, result.version_id, day)


def test_invalid_source_leaves_no_rows(database: Engine, tmp_path: Path) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(FIXTURE.read_text().replace("2026-01-10*", "2025-01-01"))
    with pytest.raises(ValueError, match="invalid audit"):
        load(database, path)
    assert not any(counts(database).values())


def test_source_evidence_preserves_whitespace_markers_and_extra_columns(
    database: Engine, tmp_path: Path
) -> None:
    path = tmp_path / "evidence.csv"
    path.write_text(
        "symbol,cik,name,sector,date_added,date_removed,created_at,note\n"
        ' beta ,0002,"Name, with comma",industrials,2026-01-05*,NA,2026-01-07,NA\n'
    )
    result = load(database, path)
    verify_source(database, result.version_id)
    with Session(database) as session:
        row = session.get(SourceRow, (result.version_id, 0))
        assert row is not None
        assert row.original_fields["date_removed"] == "NA"
        assert row.original_fields["symbol"] == " beta "
        assert row.audited_fields["note"] is None


def test_foreign_keys_reject_unlinked_records(database: Engine) -> None:
    result = load(database)
    before = counts(database)
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            insert(SourceRow).values(
                version_id=result.version_id,
                source_record_position=100,
                identity_position=999,
                record_sha256="a" * 64,
                original_fields={},
                audited_fields={},
            )
        )
    assert counts(database) == before


@pytest.mark.parametrize(
    "change_sql",
    [
        pytest.param(
            "UPDATE source_rows SET original_fields = '{}'::jsonb "
            "WHERE version_id = :version_id AND source_record_position = 0",
            id="original-fields",
        ),
        pytest.param(
            "UPDATE source_rows SET record_sha256 = repeat('a', 64) "
            "WHERE version_id = :version_id AND source_record_position = 0",
            id="record-hash",
        ),
        pytest.param(
            "UPDATE source_rows SET audited_fields = '{}'::jsonb "
            "WHERE version_id = :version_id AND source_record_position = 0",
            id="audited-fields",
        ),
        pytest.param(
            "UPDATE source_rows SET identity_position = 0 "
            "WHERE version_id = :version_id AND source_record_position = 1",
            id="identity-link",
        ),
        pytest.param(
            "UPDATE source_identities SET cik = 'WRONG' "
            "WHERE version_id = :version_id AND symbol = 'FUTURE'",
            id="identity-outside-coverage",
        ),
        pytest.param(
            "UPDATE membership_intervals SET date_added_approximate = false "
            "WHERE version_id = :version_id AND source_record_position = 1",
            id="addition-flag",
        ),
        pytest.param(
            "UPDATE membership_intervals SET date_removed_approximate = false "
            "WHERE version_id = :version_id AND source_record_position = 1",
            id="removal-flag",
        ),
        pytest.param(
            "UPDATE membership_intervals SET created_at = DATE '2026-02-02' "
            "WHERE version_id = :version_id AND source_record_position = 8",
            id="interval-outside-coverage",
        ),
        pytest.param(
            "DELETE FROM membership_intervals "
            "WHERE version_id = :version_id AND source_record_position = 8",
            id="missing-interval-without-events",
        ),
        pytest.param(
            "UPDATE source_versions SET source_columns = '[]'::jsonb "
            "WHERE id = :version_id",
            id="source-columns",
        ),
        pytest.param(
            "UPDATE source_versions SET frame_dtypes = '{}'::jsonb "
            "WHERE id = :version_id",
            id="frame-types",
        ),
    ],
)
def test_verification_rejects_changed_stored_evidence(
    database: Engine, change_sql: str
) -> None:
    result = load(database)
    with database.begin() as connection:
        connection.execute(text(change_sql), {"version_id": result.version_id})
    with pytest.raises(AssertionError):
        verify_source(database, result.version_id)


@pytest.mark.parametrize(
    "change_sql",
    [
        "UPDATE source_issues SET issue_details = 'WRONG' "
        "WHERE version_id = :version_id",
        "UPDATE source_versions SET dataset_sha256 = repeat('a', 64) "
        "WHERE id = :version_id",
        "UPDATE source_versions SET record_count = 999 WHERE id = :version_id",
        "UPDATE membership_intervals SET date_added_approximate = false "
        "WHERE version_id = :version_id AND source_record_position = 1",
    ],
    ids=["issues", "file-hash", "record-count", "interval-flag"],
)
def test_optimized_cli_verification_keeps_checks_enabled(
    database: Engine, change_sql: str
) -> None:
    result = load(database)
    env = dict(
        os.environ,
        INDEXLENS_DATABASE_URL=database.url.render_as_string(hide_password=False),
    )
    command_line = [
        sys.executable,
        "-O",
        "-m",
        "indexlens.db",
        "verify",
        str(result.version_id),
    ]
    clean = subprocess.run(
        command_line, cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["parity"] == "passed"
    with database.begin() as connection:
        connection.execute(text(change_sql), {"version_id": result.version_id})
    changed = subprocess.run(
        command_line, cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert changed.returncode == 1
    assert "AssertionError" in changed.stderr
    assert '"parity": "passed"' not in changed.stdout


def test_queries_use_stored_intervals_without_reauditing(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from indexlens import data_audit
    from indexlens.db import repository

    result = load(database)
    with database.begin() as connection:
        connection.execute(
            text(
                "UPDATE membership_intervals SET date_added = DATE '2026-01-06', "
                "date_added_approximate = false WHERE version_id = :version_id "
                "AND source_record_position = 1"
            ),
            {"version_id": result.version_id},
        )

    def unexpected_audit(*args: object, **kwargs: object) -> None:
        pytest.fail("A database query must not rerun the source audit.")

    monkeypatch.setattr(data_audit, "audit_membership", unexpected_audit)
    # Also catches a regression to the former directly imported alias.
    monkeypatch.setattr(repository, "audit_membership", unexpected_audit, raising=False)
    with Session(database) as session:
        snapshot = membership_at_db(session, result.version_id, date(2026, 1, 8))
        evidence = source_events_db(session, result.version_id).source_data
    for frame in (snapshot.data, evidence):
        assert frame.loc[1, "date_added_parsed"] == pd.Timestamp("2026-01-06")
        assert not frame.loc[1, "date_added_approximate"]
        assert frame.loc[1, "date_added"] == "2026-01-05*"
