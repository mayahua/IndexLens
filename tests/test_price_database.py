"""Tests for price evidence, repeat loads, conflicts, isolation, and rollback."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from indexlens.db.models import PriceDataset, PriceObservation, PriceRow, PriceSource
from indexlens.prices.loading import PriceLoadResult, load_price_csv
from indexlens.prices.metadata import PriceMetadata, fingerprint
from tests.conftest import migrate
from tests.test_database import counts
from tests.test_database import load as load_membership

FIXTURES = Path(__file__).parent / "fixtures"
CSV = FIXTURES / "prices_synthetic.csv"
MANIFEST = FIXTURES / "prices_synthetic.metadata.json"
pytestmark = pytest.mark.postgres


def metadata(**changes: Any) -> PriceMetadata:
    return PriceMetadata.from_dict(json.loads(MANIFEST.read_text()) | changes)


def load(engine: Engine, path: Path = CSV, **changes: Any) -> PriceLoadResult:
    return load_price_csv(engine, path, metadata(**changes), batch_size=2)


def first_observation(session: Session) -> PriceObservation:
    observation = session.scalars(
        select(PriceObservation).order_by(PriceObservation.id)
    ).first()
    assert observation is not None
    return observation


def test_evidence_missingness_conflicts_and_repeated_load(database: Engine) -> None:
    result = load(database)
    assert result.counts == dict(
        rows=7,
        rejected_keys=1,
        new_observations=4,
        equal_duplicates=1,
        conflicting_rows=1,
        rows_with_value_issues=2,
    )
    before = counts(database)
    repeat = load(database)
    assert not repeat.inserted and repeat.source_id == result.source_id
    assert counts(database) == before
    with Session(database) as session:
        source = session.get(PriceSource, result.source_id)
        assert source is not None
        assert source.source_bytes == CSV.read_bytes()
        assert source.source_sha256 == sha256(CSV.read_bytes()).hexdigest()
        assert source.declarations == metadata().declarations
        assert source.metadata_sha256 == fingerprint(source.declarations)
        assert source.retrieved_at == metadata().retrieved_at
        rows = session.scalars(
            select(PriceRow).order_by(PriceRow.record_position)
        ).all()
        assert [r.record_position for r in rows] == list(range(7))
        assert rows[0].original_fields["Adj Close"] == "102"
        assert rows[0].record_sha256 == fingerprint(rows[0].original_fields)
        assert (
            rows[0].observation_id == rows[2].observation_id == rows[4].observation_id
        )
        assert "conflicting_observation" in rows[4].issues
        assert "missing_volume" in rows[1].issues
        assert "invalid_ohlc" in rows[5].issues
        assert rows[6].observation_id is None
        obs = first_observation(session)
        assert obs.conflicted and not obs.prices_usable and not obs.volume_usable
        missing = session.get(PriceObservation, rows[1].observation_id)
        assert (
            missing is not None and missing.prices_usable and not missing.volume_usable
        )


def test_new_retrieval_retains_provenance_without_more_observations(
    database: Engine,
) -> None:
    load(database)
    second = load(database, retrieved_at="2026-10-13T12:00:00Z")
    assert second.inserted and second.counts["new_observations"] == 0
    assert counts(database)["price_observations"] == 4
    assert counts(database)["price_rows"] == 14
    assert counts(database)["price_sources"] == 2
    with Session(database) as session:
        assert first_observation(session).conflicted


def test_later_file_missingness_conflict_is_not_filled(
    database: Engine,
    tmp_path: Path,
) -> None:
    path = tmp_path / "prices.csv"
    path.write_text(
        "provider_symbol,observation_date,open,high,low,close,volume\n"
        "DEMO_A,2026-09-21,100,105,98,103,\n"
    )
    load(database, path)
    path.write_text(path.read_text().replace("103,\n", "103,0\n"))
    result = load(database, path)
    assert result.counts["conflicting_rows"] == 1
    with Session(database) as session:
        obs = first_observation(session)
        assert obs.conflicted and not obs.prices_usable
        assert obs.normalized_values["volume"] is None
        assert session.scalar(select(func.count()).select_from(PriceSource)) == 2


def test_namespace_kind_and_settings_cannot_change(database: Engine) -> None:
    load(database)
    before = counts(database)
    with pytest.raises(ValueError, match="data kind"):
        load(database, data_kind="real")
    with pytest.raises(ValueError, match="Adjustment settings"):
        load(database, adjustment_settings={"auto_adjust": True})
    assert counts(database) == before
    # A real label is used only to exercise isolation; all input here is invented.
    load(database, dataset_id="separate-real-label-test", data_kind="real")
    load(database, price_basis="different-basis")
    load(database, provider="different-provider")
    assert counts(database)["price_observations"] == 16


def test_concurrent_loads_are_repeatable(database: Engine) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: load(database), range(2)))
    assert results[0].source_id == results[1].source_id
    assert sum(r.inserted for r in results) == 1
    assert counts(database)["price_rows"] == 7


def test_different_concurrent_files_detect_conflict(
    database: Engine, tmp_path: Path
) -> None:
    header = "provider_symbol,observation_date,open,high,low,close,volume\n"
    paths = [tmp_path / "a.csv", tmp_path / "b.csv"]
    paths[0].write_text(header + "DEMO_A,2026-09-21,100,105,98,103,0\n")
    paths[1].write_text(header + "DEMO_A,2026-09-21,100,105,98,104,0\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda path: load(database, path), paths))
    with Session(database) as session:
        assert first_observation(session).conflicted
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == 1


def test_actual_late_constraint_failure_rolls_back(database: Engine) -> None:
    before = counts(database)
    with database.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE price_rows ADD CONSTRAINT fail_late "
                "CHECK (record_position < 4)"
            )
        )
    with pytest.raises(IntegrityError):
        load(database)
    assert counts(database) == before


def test_late_failure_restores_existing_observation(
    database: Engine, tmp_path: Path
) -> None:
    path = tmp_path / "prices.csv"
    header = "provider_symbol,observation_date,open,high,low,close,volume\n"
    path.write_text(header + "DEMO_A,2026-09-21,100,105,98,103,0\n")
    load(database, path)
    before = counts(database)
    path.write_text(
        header + "DEMO_A,2026-09-21,100,105,98,104,0\n"
        "DEMO_B,2026-09-21,100,105,98,103,0\n"
        "wrong,width\n"
    )
    with pytest.raises(ValueError, match="width"):
        load(database, path)
    assert counts(database) == before
    with Session(database) as session:
        obs = first_observation(session)
        assert not obs.conflicted and obs.prices_usable


@pytest.mark.parametrize(
    "payload", ["", "x,x\n", "provider_symbol,observation_date,open,open\n"]
)
def test_structural_failures_leave_no_dataset(
    database: Engine, tmp_path: Path, payload: str
) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(payload)
    with pytest.raises(ValueError):
        load(database, path)
    assert counts(database)["price_datasets"] == 0


def test_header_only_csv_and_invalid_batch(database: Engine, tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("provider_symbol,observation_date,open,high,low,close,volume\n")
    result = load(database, path)
    assert not any(result.counts.values())
    assert not load(database, path).inserted
    with pytest.raises(ValueError):
        load_price_csv(database, CSV, metadata(), batch_size=0)
    assert counts(database)["price_sources"] == 1


def test_upgrade_from_populated_source_history_and_price_only_downgrade(
    database: Engine,
) -> None:
    migrate(database, "downgrade", "0001")
    membership = load_membership(database)
    migrate(database, "upgrade", "head")
    before = counts(database)
    load(database)
    migrate(database, "downgrade", "0001")
    migrate(database, "upgrade", "head")
    assert counts(database) == before
    from indexlens.db.repository import membership_at_db

    with Session(database) as session:
        assert not membership_at_db(
            session, membership.version_id, date(2026, 1, 20)
        ).data.empty


def test_cli_repeat_and_safe_failure(database: Engine, tmp_path: Path) -> None:
    env = dict(
        os.environ,
        INDEXLENS_DATABASE_URL=database.url.render_as_string(hide_password=False),
    )
    args = [
        sys.executable,
        "-m",
        "indexlens.prices",
        str(CSV),
        "--metadata",
        str(MANIFEST),
        "--batch-size",
        "2",
    ]
    first = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    repeat = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    assert first.returncode == repeat.returncode == 0
    assert json.loads(first.stdout)["inserted"] is True
    assert json.loads(repeat.stdout)["inserted"] is False
    bad = tmp_path / "metadata.json"
    bad.write_text('{"credential_example": "do-not-log-me"}')
    failed = subprocess.run(
        args[:4] + ["--metadata", str(bad)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert "do-not-log-me" not in failed.stdout + failed.stderr


def test_database_rejects_cross_dataset_evidence(database: Engine) -> None:
    result = load(database)
    with Session(database) as session, session.begin():
        session.add(PriceDataset(dataset_id="other", data_kind="synthetic"))
    with pytest.raises(IntegrityError), Session(database) as session, session.begin():
        row = session.get(PriceRow, (result.source_id, 0))
        assert row is not None
        row.dataset_id = "other"


def test_conflicting_row_declarations_rollback(
    database: Engine, tmp_path: Path
) -> None:
    path = tmp_path / "mixed.csv"
    path.write_text(
        "provider_symbol,observation_date,data_kind\nDEMO_A,2026-09-21,real\n"
    )
    with pytest.raises(ValueError, match="disagree"):
        load(database, path)
    assert counts(database)["price_datasets"] == 0
