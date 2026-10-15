"""Tests for reviewed joins, source isolation, common dates and feature CLI."""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, event, select
from sqlalchemy.orm import Session

from indexlens.db.loading import load_source_csv
from indexlens.db.models import PriceObservation, PriceRow, PriceSource
from indexlens.features.query import query_features
from indexlens.features.review import Review
from indexlens.prices.loading import load_price_csv
from tests.test_price_database import metadata

pytestmark = pytest.mark.postgres
DAY = date(2026, 9, 21)


def setup(database: Engine, tmp_path: Path) -> dict[str, Any]:
    membership = tmp_path / "members.csv"
    membership.write_text(
        "symbol,cik,name,sector,date_added,date_removed,created_at\n"
        "B,0001,Beta,Tech,2025-01-01,,2025-01-01\n"
        "A,0001,Alpha,Tech,2025-01-01,,2025-01-01\n"
    )
    loaded = load_source_csv(
        database,
        membership,
        source="synthetic-features",
        dataset_kind="synthetic",
        coverage_start=date(2026, 1, 1),
        coverage_end=DAY,
    )
    path = tmp_path / "prices.csv"
    lines = ["provider_symbol,observation_date,open,high,low,close,volume"]
    for symbol in ("B", "A"):
        for i in range(22):
            value = 110 if i == 21 else 100
            day = DAY - timedelta(days=21 - i)
            lines.append(f"{symbol},{day},{value},{value},{value},{value},{i}")
    path.write_text("\n".join(lines) + "\n")
    meta = metadata(dataset_id="synthetic-features", provider="fixture")
    result = load_price_csv(database, path, meta)
    with Session(database) as session:
        source = session.get(PriceSource, result.source_id)
        assert source is not None
        pin = dict(
            source_sha256=source.source_sha256, metadata_sha256=source.metadata_sha256
        )
    decision = dict(
        status="approved",
        start="2026-08-01",
        end="2026-10-31",
        evidence=["Invented software test evidence; not real securities"],
    )
    return dict(
        review_version="feature-review-v1",
        review_id="synthetic-review",
        reviewed_on="2026-10-13",
        dataset_id=meta.dataset_id,
        data_kind="synthetic",
        provider="fixture",
        price_basis=meta.price_basis,
        adjustment_settings=meta.adjustment_settings,
        limitations="Synthetic fixture only",
        membership_sha256=loaded.dataset_sha256,
        basis_review=decision | dict(description="Synthetic close"),
        universe=[
            decision
            | dict(
                symbol=symbol,
                cik="0001",
                provider_symbol=symbol,
                source_record_positions=[i],
                sources=[pin],
            )
            for i, symbol in enumerate(("B", "A"))
        ],
    )


def query(database: Engine, raw: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    with Session(database) as session:
        return query_features(session, Review.from_dict(raw), DAY, **kwargs)


def test_group_isolation_ties_provenance_and_membership(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    result = query(database, raw, membership=True)
    assert result["data_kind"] == "synthetic"
    assert result["ranking_complete"]
    assert [row["symbol"] for row in result["results"]] == ["A", "B"]
    assert [row["rank"] for row in result["results"]] == [1, 2]
    for row in result["results"]:
        assert row["features"]["return_20"] == "0.1"
        assert row["member_at_reference"]
        assert len(row["inputs"]) == 21
        assert row["inputs"][0]["provenance"][0]["record_sha256"]
    assert result["membership_context"]["reference_date"] == DAY.isoformat()
    # Same CIK does not combine symbols, and an unrelated basis/provider/dataset
    # cannot supply missing observations or change this group's values.
    for changes in (
        {"price_basis": "other"},
        {"provider": "other"},
        {"dataset_id": "other"},
    ):
        load_price_csv(
            database,
            tmp_path / "prices.csv",
            metadata(
                **(dict(dataset_id="synthetic-features", provider="fixture") | changes)
            ),
        )
    assert (
        query(database, raw)["results"][0]["features"]
        == result["results"][0]["features"]
    )


@pytest.mark.parametrize("gate", ["mapping", "basis", "source", "identity", "settings"])
def test_gate_failures_remain_in_universe(
    database: Engine, tmp_path: Path, gate: str
) -> None:
    raw = setup(database, tmp_path)
    mapping = raw["universe"][0]
    if gate == "mapping":
        mapping["status"] = "unresolved"
    elif gate == "basis":
        raw["basis_review"]["status"] = "unresolved"
    elif gate == "source":
        mapping["sources"][0] = dict(source_sha256="0" * 64, metadata_sha256="0" * 64)
    elif gate == "identity":
        mapping["cik"] = "9999"
    else:
        raw["adjustment_settings"] = {"auto_adjust": True}
    result = query(database, raw)
    assert not result["ranking_complete"]
    assert len(result["results"]) == 2
    row = next(r for r in result["results"] if r["symbol"] == "B")
    assert row["rank"] is None and all(v is None for v in row["features"].values())


@pytest.mark.parametrize("gate", ["mapping", "basis"])
def test_every_input_date_is_reviewed(
    database: Engine, tmp_path: Path, gate: str
) -> None:
    raw = setup(database, tmp_path)
    decision = raw["universe"][0] if gate == "mapping" else raw["basis_review"]
    decision["start"] = (DAY - timedelta(days=19)).isoformat()
    result = query(database, raw)
    row = next(r for r in result["results"] if r["symbol"] == "B")
    assert row["features"]["return_20"] is None
    assert row["features"]["average_volume_20"] is not None
    assert f"outside_{gate}_dates" in row["unavailable"]["return_20"]


def test_conflicting_later_source_is_unavailable(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    path = tmp_path / "conflict.csv"
    path.write_text(
        "provider_symbol,observation_date,open,high,low,close,volume\n"
        f"A,{DAY},120,120,120,120,21\n"
    )
    load_price_csv(
        database, path, metadata(dataset_id="synthetic-features", provider="fixture")
    )
    result = query(database, raw)
    row = next(r for r in result["results"] if r["symbol"] == "A")
    assert "conflicting_observation" in row["unavailable"]["return_20"]
    assert len(row["inputs"][-1]["provenance"]) == 2
    assert row["rank"] is None
    assert [r["symbol"] for r in result["results"]] == ["B", "A"]


def test_common_date_does_not_silently_use_stale_rows(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    raw["universe"][0]["provider_symbol"] = "MISSING"
    result = query(database, raw)
    assert not result["common_observation_date"]
    assert len(result["results"]) == 2
    assert all(r["rank"] is None for r in result["results"])
    with pytest.raises(ValueError, match="common"):
        query(database, raw, membership=True)


def test_kind_and_membership_date_guards(database: Engine, tmp_path: Path) -> None:
    raw = setup(database, tmp_path)
    raw["data_kind"] = "real"
    with pytest.raises(ValueError, match="real/synthetic"):
        query(database, raw)
    raw["data_kind"] = "synthetic"
    # Use an observed date before reviewed membership coverage.
    with Session(database) as session:
        from indexlens.db.models import SourceVersion

        version = session.scalar(select(SourceVersion))
        assert version is not None
        version.coverage_end = DAY - timedelta(days=1)
        session.commit()
    assert query(database, raw)["ranking_complete"]
    with pytest.raises(ValueError, match="coverage"):
        query(database, raw, membership=True)


def test_cli_success_unavailable_and_safe_error(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(raw))
    env = dict(
        os.environ,
        INDEXLENS_DATABASE_URL=database.url.render_as_string(hide_password=False),
    )
    args = [
        sys.executable,
        "-m",
        "indexlens.features",
        "--review",
        str(path),
        "--date",
        DAY.isoformat(),
        "--membership",
    ]
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ranking_complete"]
    raw["basis_review"]["status"] = "unresolved"
    path.write_text(json.dumps(raw))
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert len(json.loads(result.stdout)["results"]) == 2
    path.write_text('{"secret": "must-not-print"}')
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 1
    assert "must-not-print" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("changes", "prices", "volume"),
    [
        pytest.param({6: ""}, True, False, id="missing_volume"),
        pytest.param({5: ""}, False, True, id="missing_close"),
        pytest.param({3: "1"}, False, False, id="invalid_ohlc"),
        pytest.param({2: ""}, True, True, id="missing_open"),
        pytest.param({3: ""}, True, True, id="missing_high"),
        pytest.param({4: ""}, True, True, id="missing_low"),
        pytest.param({2: "", 3: "", 4: ""}, True, True, id="close_only"),
        pytest.param({2: "bad"}, False, False, id="invalid_open"),
        pytest.param({3: "NaN"}, False, False, id="nonfinite_high"),
        pytest.param({4: "-1"}, False, False, id="negative_low"),
        pytest.param({5: "0"}, False, False, id="zero_close"),
        pytest.param({6: "bad"}, True, False, id="invalid_volume"),
        pytest.param({2: "", 3: "bad"}, False, False, id="missing_and_invalid"),
    ],
)
def test_normalized_bad_rows_are_not_skipped(
    database: Engine,
    tmp_path: Path,
    changes: dict[int, str],
    prices: bool,
    volume: bool,
) -> None:
    raw = setup(database, tmp_path)
    lines = (tmp_path / "prices.csv").read_text().splitlines()
    fields = lines[11].split(",")
    for column, value in changes.items():
        fields[column] = value
    lines[11] = ",".join(fields)
    path = tmp_path / "bad-values.csv"
    path.write_text("\n".join(lines) + "\n")
    loaded = load_price_csv(
        database, path, metadata(dataset_id="incomplete", provider="fixture")
    )
    raw["dataset_id"] = "incomplete"
    with Session(database) as session:
        source = session.get(PriceSource, loaded.source_id)
        assert source is not None
        for mapping in raw["universe"]:
            mapping["sources"] = [
                dict(
                    source_sha256=source.source_sha256,
                    metadata_sha256=source.metadata_sha256,
                )
            ]
    result = query(database, raw)
    a, b = [
        next(r for r in result["results"] if r["symbol"] == symbol)
        for symbol in ("A", "B")
    ]
    assert a["features"]["return_20"] == "0.1"
    assert b["features"]["return_20"] == ("0.1" if prices else None)
    if prices:
        assert float(b["features"]["volatility_20"]) == pytest.approx(0.0005**0.5)
    else:
        assert b["features"]["volatility_20"] is None
    assert b["features"]["average_volume_20"] == ("11.5" if volume else None)
    assert len(b["inputs"]) == 21
    changed_input = next(
        row for row in b["inputs"] if row["observation_date"] == fields[1]
    )
    assert changed_input["close_usable"] == prices
    # A missing non-closing price must not rewrite ingestion's complete-bar flag.
    if prices and any(column in changes for column in (2, 3, 4)):
        with Session(database) as session:
            observation = session.scalar(
                select(PriceObservation).where(
                    PriceObservation.dataset_id == "incomplete",
                    PriceObservation.provider_symbol == "B",
                    PriceObservation.observation_date == date.fromisoformat(fields[1]),
                )
            )
            assert observation is not None and not observation.prices_usable


def test_stale_member_prevents_common_date_ranking(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    with Session(database) as session, session.begin():
        observation = session.scalar(
            select(PriceObservation).where(
                PriceObservation.provider_symbol == "B",
                PriceObservation.observation_date == DAY,
            )
        )
        assert observation is not None
        session.execute(
            delete(PriceRow).where(PriceRow.observation_id == observation.id)
        )
        session.delete(observation)
    result = query(database, raw)
    assert not result["common_observation_date"]
    assert all(row["rank"] is None for row in result["results"])
    stale = next(row for row in result["results"] if row["symbol"] == "B")
    assert "missing_reference_observation" in stale["unavailable"]["return_20"]


def test_review_structure_is_fail_closed(database: Engine, tmp_path: Path) -> None:
    raw = setup(database, tmp_path)
    raw["universe"].append(raw["universe"][0])
    with pytest.raises(ValueError, match="duplicate"):
        Review.from_dict(raw)
    raw["universe"].pop()
    raw["universe"][0]["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        Review.from_dict(raw)
    raw["universe"][0]["evidence"] = ["test fixture"]
    raw["universe"][0]["sources"] = [12]
    with pytest.raises(ValueError, match="Source pin"):
        Review.from_dict(raw)


def test_source_records_are_fetched_together_and_all_reviewed(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    baseline = query(database, raw)
    duplicate = load_price_csv(
        database,
        tmp_path / "prices.csv",
        metadata(
            dataset_id="synthetic-features",
            provider="fixture",
            source_reference="second synthetic source",
        ),
    )
    unreviewed = query(database, raw)
    assert all(
        "unreviewed_source" in row["unavailable"]["return_20"]
        for row in unreviewed["results"]
    )
    with Session(database) as session:
        source = session.get(PriceSource, duplicate.source_id)
        assert source is not None
        for mapping in raw["universe"]:
            mapping["sources"].append(
                dict(
                    source_sha256=source.source_sha256,
                    metadata_sha256=source.metadata_sha256,
                )
            )
    evidence_queries = []

    def record_query(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "FROM price_rows" in statement and "JOIN price_sources" in statement:
            evidence_queries.append(statement)

    event.listen(database, "before_cursor_execute", record_query)
    try:
        result = query(database, raw)
    finally:
        event.remove(database, "before_cursor_execute", record_query)
    # Two securities need two evidence reads, not 42 reads for their 21 rows.
    assert len(evidence_queries) == 2
    assert result["feature_version"] == "observed-20-v2"
    assert result["ranking_complete"]
    assert [row["features"] for row in result["results"]] == [
        row["features"] for row in baseline["results"]
    ]
    with Session(database) as session:
        for row in result["results"]:
            for observation in row["inputs"]:
                provenance = observation["provenance"]
                assert len(provenance) == 2
                assert len({entry["source_id"] for entry in provenance}) == 2
                for entry in provenance:
                    saved = session.get(
                        PriceRow, (entry["source_id"], entry["record_position"])
                    )
                    assert saved is not None
                    assert saved.original_fields["provider_symbol"] == row["symbol"]
                    assert (
                        saved.original_fields["observation_date"]
                        == observation["observation_date"]
                    )
                    close = saved.normalized_values["close"]
                    assert close is not None
                    assert Decimal(close) == Decimal(observation["close"])


def test_missing_source_records_remain_unavailable(
    database: Engine, tmp_path: Path
) -> None:
    raw = setup(database, tmp_path)
    with Session(database) as session, session.begin():
        observation = session.scalar(
            select(PriceObservation).where(
                PriceObservation.provider_symbol == "B",
                PriceObservation.observation_date == DAY,
            )
        )
        assert observation is not None
        session.execute(
            delete(PriceRow).where(PriceRow.observation_id == observation.id)
        )
    result = query(database, raw)
    b = next(row for row in result["results"] if row["symbol"] == "B")
    assert b["rank"] is None and len(b["inputs"]) == 21
    for feature in ("return_20", "volatility_20", "average_volume_20"):
        assert "missing_source_evidence" in b["unavailable"][feature]
    assert b["inputs"][-1]["provenance"] == []
