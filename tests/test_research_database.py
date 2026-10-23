"""Tests for stored research joins, full population retention and CLI run records."""

import gzip
import json
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import Engine, event, select, update
from sqlalchemy.orm import Session

from indexlens.db.loading import load_source_csv
from indexlens.db.models import PriceObservation, PriceSource, SourceVersion
from indexlens.features.core import calculate
from indexlens.features.query import reviewed_history
from indexlens.features.review import Review
from indexlens.prices.loading import load_price_csv
from indexlens.prices.metadata import fingerprint
from indexlens.research import __main__ as research_cli
from indexlens.research.__main__ import execute, main
from indexlens.research.workflow import run_research
from tests.test_price_database import metadata

pytestmark = pytest.mark.postgres


def setup(
    database: Engine, tmp_path: Path, *, historical_alias: bool = False
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    membership = tmp_path / "membership.csv"
    membership.write_text(
        "symbol,cik,name,sector,date_added,date_removed,created_at\n"
        "A,0001,Alpha,Tech,2025-01-01,2025-10-15,2025-01-01\n"
        "A,0001,Alpha,Tech,2026-02-01,2026-06-15,2026-02-01\n"
        "B,0002,Beta,Tech,2025-01-01,,2025-01-01\n"
        "C,0003,Gamma,Tech,2025-01-01*,,2025-01-01\n"
    )
    if historical_alias:
        membership.write_text(
            membership.read_text().replace(
                "A,0001,Alpha,Tech,2025-01-01", "OLD,0001,Alpha,Tech,2025-01-01"
            )
        )
    loaded = load_source_csv(
        database,
        membership,
        source="synthetic-removal",
        dataset_kind="synthetic",
        coverage_start=date(2025, 1, 1),
        coverage_end=date(2026, 9, 30),
    )
    price = tmp_path / "prices.csv"
    lines = ["provider_symbol,observation_date,open,high,low,close,volume"]
    days = pd.bdate_range("2025-08-01", "2026-08-03")
    for symbol in ("A", "B"):
        for i, stamp in enumerate(days):
            value = 100 + i % 9 + (i / 10 if symbol == "B" else i / 5)
            lines.append(
                f"{symbol},{stamp.date()},{value},{value},{value},{value},{100 + i}"
            )
    price.write_text("\n".join(lines) + "\n")
    meta = metadata(dataset_id="synthetic-removal", provider="fixture")
    price_loaded = load_price_csv(database, price, meta)
    with Session(database) as session:
        source = session.get(PriceSource, price_loaded.source_id)
        assert source is not None
        pin = dict(
            source_sha256=source.source_sha256, metadata_sha256=source.metadata_sha256
        )
    identities = [
        dict(symbol="A", cik="0001", source_record_positions=[0, 1]),
        dict(symbol="B", cik="0002", source_record_positions=[2]),
    ]
    if historical_alias:
        identities[0]["source_record_positions"] = [1]
        identities.append(dict(symbol="OLD", cik="0001", source_record_positions=[0]))
    manifest = dict(
        data_kind="synthetic",
        version="synthetic-removal-v1",
        source_sha256=loaded.dataset_sha256,
        coverage_start="2025-01-01",
        coverage_end="2026-09-30",
        train_reference_start="2025-09-30",
        train_outcome_cutoff="2025-12-31",
        test_reference_start="2026-05-31",
        test_reference_end="2026-07-31",
        label_horizon_days=30,
        price_request_envelope=dict(
            start_inclusive="2025-08-01", end_exclusive="2026-08-01"
        ),
        eligible_source_identity_count=len(identities),
        identities=identities,
    )
    decision = dict(
        status="approved",
        start="2025-08-01",
        end="2026-07-31",
        evidence=["Invented 2025–2026 fixture; not real securities."],
    )
    review: dict[str, Any] = dict(
        review_version="feature-review-v1",
        review_id="synthetic-removal-v1",
        reviewed_on="2026-10-15",
        dataset_id=meta.dataset_id,
        data_kind="synthetic",
        provider="fixture",
        price_basis=meta.price_basis,
        adjustment_settings=meta.adjustment_settings,
        membership_sha256=loaded.dataset_sha256,
        limitations="Synthetic software tests only.",
        basis_review=decision | dict(description="Invented close/volume"),
        universe=[
            identity
            | decision
            | dict(provider_symbol=identity["symbol"], sources=[pin])
            for identity in identities
        ],
    )
    ingestion: dict[str, Any] = dict(
        dataset_id=meta.dataset_id,
        results=[dict(source_identity=i, status="loaded") for i in identities],
    )
    if historical_alias:
        review["universe"][-1].update(
            provider_symbol="A", historical_alias_of="A", end="2025-10-14"
        )
        ingestion["results"][-1]["status"] = "failed"
    return manifest, review, ingestion


def run(
    database: Engine,
    bundle: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    cutoff: float = 0.05,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest, review, ingestion = bundle
    with Session(database) as session:
        return run_research(
            session, manifest, Review.from_dict(review), ingestion, 42, cutoff
        )


def test_full_pipeline_retains_exclusions_dates_and_event_counts(
    database: Engine, tmp_path: Path
) -> None:
    bundle = setup(database, tmp_path)
    version_queries = []

    def record_query(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "FROM source_versions" in statement:
            version_queries.append(statement)

    event.listen(database, "before_cursor_execute", record_query)
    try:
        rows, report = run(database, bundle)
    finally:
        event.remove(database, "before_cursor_execute", record_query)
    assert len(version_queries) == 1
    assert report["data_kind"] == "synthetic"
    assert len(report["identities"]) == 3
    assert all(
        not r["eligible"] and r["label_after_source_screen"] is None
        for r in rows
        if r["symbol"] == "C"
    )
    for split in ("train", "test"):
        counts = report["summaries"][split]["eligible"]
        assert counts["observations"] == 4 and counts["positive_observations"] == 1
        assert counts["distinct_removal_events"] == 1
    row = next(
        r for r in rows if r["symbol"] == "A" and r["reference_date"] == "2026-05-31"
    )
    assert row["feature_observation_date"] == "2026-05-29"
    assert row["source_record_positions"] == row["removal_event_positions"] == [1]
    assert row["windows"]["return_20"][-1] == "2026-05-29"
    assert report["evaluation"]["baseline"]["probability"] == 0.25
    assert report["evaluation"]["status"] == "evaluated"
    assert all(
        max(r["windows"]["return_20"], default="") <= r["reference_date"] for r in rows
    )


@pytest.mark.parametrize(
    "gate", ["mapping", "basis", "pin", "interval", "population", "kind"]
)
def test_research_approval_and_population_restrictions(
    database: Engine, tmp_path: Path, gate: str
) -> None:
    bundle = setup(database, tmp_path)
    manifest, review, _ = bundle
    if gate == "mapping":
        review["universe"][0]["status"] = "unresolved"
    elif gate == "basis":
        review["basis_review"]["start"] = "2026-05-28"
    elif gate == "pin":
        review["universe"][0]["sources"][0] = dict(
            source_sha256="0" * 64, metadata_sha256="0" * 64
        )
    elif gate == "interval":
        review["universe"][0]["source_record_positions"] = [0]
    elif gate == "population":
        manifest["identities"] = manifest["identities"][:1]
        manifest["eligible_source_identity_count"] = 1
    else:
        review["data_kind"] = "real"
    if gate in {"interval", "population", "kind"}:
        with pytest.raises(ValueError):
            run(database, bundle)
    else:
        rows, report = run(database, bundle)
        row = next(
            r
            for r in rows
            if r["symbol"] == "A" and r["reference_date"] == "2026-05-31"
        )
        assert not row["eligible"] and row["features"]["return_20"] is None
        assert report["evaluation"]["logistic"] is None


@pytest.mark.parametrize("problem", ["missing_reference", "conflict", "missing_close"])
def test_bad_window_positions_are_not_replaced(
    database: Engine, tmp_path: Path, problem: str
) -> None:
    bundle = setup(database, tmp_path)
    with Session(database) as session, session.begin():
        target = session.scalar(
            select(PriceObservation).where(
                PriceObservation.provider_symbol == "A",
                PriceObservation.observation_date
                == date(2026, 5, 29 if problem == "missing_reference" else 20),
            )
        )
        assert target is not None
        if problem == "missing_reference":
            # A completely absent final session cannot become Thursday's observation.
            session.execute(
                update(PriceObservation)
                .where(PriceObservation.id == target.id)
                .values(observation_date=date(2026, 8, 4))
            )
        elif problem == "conflict":
            target.conflicted = True
        else:
            target.normalized_values = target.normalized_values | {"close": None}
    rows, _ = run(database, bundle)
    row = next(
        r for r in rows if r["symbol"] == "A" and r["reference_date"] == "2026-05-31"
    )
    assert not row["eligible"] and row["features"]["return_20"] is None
    if problem != "missing_reference":
        assert "2026-05-20" in row["windows"]["return_20"]
        assert len(row["windows"]["return_20"]) == 21
    else:
        assert row["latest_observed_date"] == "2026-05-28"
        assert "missing_reference_observation" in row["exclusion_reasons"]


def test_history_reuses_feature_gates_and_ignores_future_prices(
    database: Engine, tmp_path: Path
) -> None:
    bundle = setup(database, tmp_path)
    before, _ = run(database, bundle)
    with Session(database) as session, session.begin():
        session.execute(
            update(PriceObservation)
            .where(PriceObservation.observation_date > date(2026, 7, 31))
            .values(conflicted=True)
        )
    after, _ = run(database, bundle)
    assert before == after
    with Session(database) as session:
        history = reviewed_history(
            session,
            Review.from_dict(bundle[1]),
            bundle[1]["universe"][0],
            date(2025, 8, 1),
            date(2026, 5, 29),
        )
        expected = calculate(history, date(2026, 5, 29))
    row = next(
        r for r in after if r["symbol"] == "A" and r["reference_date"] == "2026-05-31"
    )
    assert row["features"] == {k: str(v) for k, v in expected.values.items()}


def test_cli_run_record_and_repeatability(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = setup(database, tmp_path)
    # Keep source code in the repository and generated outputs under data/.
    root = Path.cwd()
    output = root / "data" / ("test-research-" + tmp_path.name)
    paths = [
        tmp_path / (name + ".json") for name in ("manifest", "review", "ingestion")
    ]
    paths[0].write_text(json.dumps(bundle[0]))
    bundle[2]["manifest_sha256"] = fingerprint(bundle[0])
    for path, payload in zip(paths[1:], bundle[1:], strict=True):
        path.write_text(json.dumps(payload))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            dict(
                config_version="removal-run-v1",
                seed=42,
                manifest=str(paths[0]),
                review=str(paths[1]),
                ingestion_report=str(paths[2]),
            )
        )
    )
    monkeypatch.setenv(
        "INDEXLENS_DATABASE_URL", database.url.render_as_string(hide_password=False)
    )
    try:
        report = execute(config, output)
        assert report["code"]["revision"] and report["code"]["file_sha256"]
        assert report["package_versions"]["scikit-learn"]
        saved = json.loads((output / "report.json").read_text())
        assert saved["evaluation"]["threshold"] == 0.05
        for model in ("baseline", "logistic"):
            assert saved["evaluation"][model]["metrics"]["threshold"] == 0.05
        assert saved["evaluation"]["baseline"]["metrics"]["confusion_matrix"] == [
            [0, 3],
            [0, 1],
        ]
        assert report["observation_artifact"]["rows"] == 14
        with gzip.open(output / "observations.jsonl.gz", "rt") as stream:
            assert len(list(stream)) == 14
        assert main(["--config", str(config), "--output", str(output)]) == 1
        assert "Choose a new output directory" in capsys.readouterr().err
        assert (
            main(["--config", str(config), "--output", str(tmp_path / "outside")]) == 1
        )
        assert (
            "Generated research outputs must be under data/" in capsys.readouterr().err
        )
        # Separate output, identical model and data fingerprints.
        repeated = execute(config, output / "repeat")
        assert repeated["evaluation"] == report["evaluation"]
        assert repeated["observation_artifact"] == report["observation_artifact"]
        original_config = config.read_bytes()
        config.write_text(
            json.dumps(
                json.loads(original_config)
                | {
                    "config_version": "removal-run-v2",
                    "cutoff": 0.002,
                }
            )
        )
        selected = execute(config, output / "selected")
        assert selected["configuration"]["cutoff"] == 0.002
        assert selected["evaluation"]["threshold"] == 0.002
        for name in ("baseline", "logistic"):
            assert selected["evaluation"][name]["metrics"]["threshold"] == 0.002
        assert (
            selected["evaluation"]["predictions"] == report["evaluation"]["predictions"]
        )
        assert "0.002 classification cutoff" in selected["limitations"][-1]
        override = output / "override"
        assert (
            main(
                [
                    "--config",
                    str(config),
                    "--output",
                    str(override),
                    "--cutoff",
                    "0.0016",
                ]
            )
            == 0
        )
        overridden = json.loads((override / "report.json").read_text())
        assert overridden["configuration"]["cutoff"] == 0.0016
        assert overridden["evaluation"]["threshold"] == 0.0016
        assert (
            overridden["evaluation"]["predictions"]
            == report["evaluation"]["predictions"]
        )
        config.write_bytes(original_config)

        def run_while_config_changes(
            session: Session,
            manifest: dict[str, Any],
            review: Review,
            ingestion: dict[str, Any],
            seed: int,
            cutoff: float,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            config.write_text(json.dumps(json.loads(original_config) | {"seed": 43}))
            return run_research(session, manifest, review, ingestion, seed, cutoff)

        monkeypatch.setattr(research_cli, "run_research", run_while_config_changes)
        changed_output = output / "config_changed"
        changed = execute(config, changed_output)
        assert json.loads(config.read_bytes())["seed"] == 43
        assert changed["configuration"] == json.loads(original_config) | {
            "config_version": "removal-run-v2",
            "cutoff": 0.05,
        }
        assert (changed_output / "config.input.json").read_bytes() == original_config
        assert (
            json.loads((changed_output / "config.json").read_text())
            == changed["configuration"]
        )
        assert (
            changed["input_files"]["config"]["sha256"]
            == sha256(original_config).hexdigest()
        )
        assert changed["evaluation"] == report["evaluation"]
    finally:
        import shutil

        if output.exists():
            shutil.rmtree(output)


def test_failed_provider_and_mapping_window_keep_identity_unavailable(
    database: Engine, tmp_path: Path
) -> None:
    bundle = setup(database, tmp_path)
    bundle[2]["results"][0]["status"] = "failed"
    rows, report = run(database, bundle)
    assert len(report["identities"]) == 3
    assert all(
        not r["eligible"] and "provider_failed" in r["exclusion_reasons"]
        for r in rows
        if r["symbol"] == "A"
    )
    bundle[2]["results"][0]["status"] = "loaded"
    bundle[1]["universe"][0]["start"] = "2026-05-28"
    rows, _ = run(database, bundle)
    row = next(
        r for r in rows if r["symbol"] == "A" and r["reference_date"] == "2026-05-31"
    )
    assert not row["eligible"]
    assert "outside_mapping_dates" in row["exclusion_reasons"]


def test_explicit_historical_alias_reuses_stored_evidence_and_retains_failure(
    database: Engine, tmp_path: Path
) -> None:
    bundle = setup(database, tmp_path, historical_alias=True)
    rows, report = run(database, bundle)
    old = next(r for r in rows if r["symbol"] == "OLD")
    assert old["eligible"] and old["label_after_source_screen"] == 1
    assert old["source_record_positions"] == old["removal_event_positions"] == [0]
    assert old["price_history_identity"] == dict(symbol="A", cik="0001")
    identity = next(r for r in report["identities"] if r["symbol"] == "OLD")
    assert identity["ingestion"]["status"] == "failed"
    assert identity["price_history_ingestion"]["status"] == "loaded"
    bundle[2]["results"][0]["status"] = "failed"
    failed, _ = run(database, bundle)
    assert all(not r["eligible"] for r in failed if r["symbol"] == "OLD")
    bundle[2]["results"][0]["status"] = "loaded"
    bundle[1]["universe"][-1]["start"] = "2025-09-29"
    bounded, _ = run(database, bundle)
    assert all(
        "outside_mapping_dates" in r["exclusion_reasons"]
        for r in bounded
        if r["symbol"] == "OLD"
    )


@pytest.mark.parametrize(
    "restriction",
    [
        "implicit",
        "cik",
        "pins",
        "dates",
        "compact_start",
        "compact_end",
        "owner",
        "unresolved",
        "chain",
    ],
)
def test_shared_history_requires_explicit_consistent_alias_review(
    database: Engine, tmp_path: Path, restriction: str
) -> None:
    _, review, _ = setup(database, tmp_path, historical_alias=True)
    alias = review["universe"][-1]
    if restriction == "implicit":
        alias.pop("historical_alias_of")
    elif restriction == "cik":
        alias["cik"] = "0009"
    elif restriction == "pins":
        alias["sources"] = [dict(source_sha256="0" * 64, metadata_sha256="1" * 64)]
    elif restriction == "dates":
        alias["start"] = "2025-07-01"
    elif restriction == "compact_start":
        alias.update(start="20250101", end="20251014")
    elif restriction == "compact_end":
        review["universe"][0]["end"] = "20260731"
        alias["end"] = "2026-08-01"
    elif restriction == "owner":
        alias["historical_alias_of"] = "MISSING"
    elif restriction == "unresolved":
        review["universe"][0]["status"] = "unresolved"
    else:
        review["universe"][0]["historical_alias_of"] = "OLD"
    with pytest.raises(ValueError):
        Review.from_dict(review)


def test_shared_history_accepts_equivalent_date_formats(
    database: Engine, tmp_path: Path
) -> None:
    _, review, _ = setup(database, tmp_path, historical_alias=True)
    review["universe"][0].update(start="20250801", end="20260731")
    review["universe"][-1].update(start="2025-08-01", end="2026-07-31")
    assert Review.from_dict(review).declarations == review


def test_history_reuse_rejects_membership_version_from_another_review(
    database: Engine, tmp_path: Path
) -> None:
    _, raw, _ = setup(database, tmp_path)
    with Session(database) as session:
        version = session.scalars(select(SourceVersion)).one()
        raw["membership_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="Membership version does not match"):
            reviewed_history(
                session,
                Review.from_dict(raw),
                raw["universe"][0],
                date(2025, 8, 1),
                date(2026, 5, 29),
                version=version,
            )
