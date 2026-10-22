"""Tests for API discovery, source semantics, bounded queries and saved research."""

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from indexlens.api.app import create_app
from indexlens.api.repository import Repository, read_json
from indexlens.api.service import ApiError, bounded
from indexlens.data_audit import audit_membership
from indexlens.db.models import SourceVersion
from indexlens.features.query import query_features
from indexlens.features.review import Review
from indexlens.membership import build_source_events, membership_at, prepare_history
from tests.test_database import END, FIXTURE, START, load
from tests.test_feature_database import DAY, setup
from tests.test_research_database import run as research_run
from tests.test_research_database import setup as research_setup

PREFIX = "/api/v1"


def configuration(
    tmp_path: Path,
    version_id: int,
    review: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> Path:
    config: dict[str, Any] = dict(
        indices={"sample": dict(version_id=version_id, label="Synthetic example")},
        reviews={},
        runs={},
    )
    if review is not None:
        (tmp_path / "review.json").write_text(json.dumps(review))
        config["reviews"]["sample"] = "review.json"
    if report is not None:
        (tmp_path / "report.json").write_text(json.dumps(report))
        config["runs"]["saved"] = "report.json"
    path = tmp_path / "api.json"
    path.write_text(json.dumps(config))
    return path


@pytest.fixture
def client(database: Engine, tmp_path: Path) -> Iterator[TestClient]:
    loaded = load(database)
    with TestClient(
        create_app(configuration(tmp_path, loaded.version_id), database)
    ) as http:
        yield http


def data(client: TestClient, path: str) -> dict[str, Any]:
    response = client.get(PREFIX + path)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


@pytest.mark.postgres
def test_discovery_pagination_and_identity_intervals(client: TestClient) -> None:
    assert data(client, "/health") == {"status": "ok"}
    index = data(client, "/indices")["items"][0]
    assert index["data_kind"] == "synthetic"
    assert index["coverage_start"] == "2026-01-01"
    first = data(client, "/indices/sample/securities?limit=3")
    second = data(client, "/indices/sample/securities?limit=3&offset=3")
    assert first["total"] == 8 and first["next_offset"] == 3
    assert not {r["security_id"] for r in first["items"]} & {
        r["security_id"] for r in second["items"]
    }
    rows = data(client, "/indices/sample/securities")["items"]
    first_id = rows[0]["security_id"]
    assert first_id.endswith("-s0")
    assert data(client, f"/securities/{first_id}")["total"] > 0
    dual = [row for row in rows if row["symbol"] == "DUAL"]
    assert len(dual) == 2 and dual[0]["security_id"] != dual[1]["security_id"]
    same_cik = [row for row in rows if row["cik"] == "0001"]
    assert len(same_cik) == 2
    beta = next(row for row in rows if row["symbol"] == "BETA")
    intervals = data(client, f"/securities/{beta['security_id']}")["items"]
    assert [r["source_record_position"] for r in intervals] == [1, 2]
    assert intervals[0]["date_added_approximate"]
    assert all(row["record_sha256"] and row["original_fields"] for row in intervals)
    empty = data(client, "/indices/sample/securities?offset=99")
    assert empty["items"] == [] and empty["next_offset"] is None


@pytest.mark.postgres
def test_membership_and_events_match_in_memory_at_all_boundaries(
    client: TestClient,
) -> None:
    import pandas as pd

    raw = pd.read_csv(FIXTURE, dtype="string")
    source = data(client, "/indices")["items"][0]
    history = prepare_history(
        audit_membership(raw),
        source=source["source"],
        dataset_sha256=source["membership_sha256"],
        coverage_start=START,
        coverage_end=END,
    )
    for n in range(31):
        day = START + timedelta(days=n)
        expected = membership_at(history, day)
        response = data(client, f"/indices/sample/membership?as_of={day}")
        assert [r["source_record_position"] for r in response["items"]] == list(
            expected.data.source_record_position
        )
        assert [i["issue_type"] for i in response["metadata"]["issues"]] == [
            i.issue_type for i in expected.issues
        ]
    response = data(client, "/indices/sample/events?start=2026-01-01&end=2026-01-31")
    expected_events = build_source_events(history).data
    event_rows: Any = expected_events.itertuples(index=False)
    assert [
        (r["source_record_position"], r["event_type"], r["event_date"])
        for r in response["items"]
    ] == [
        (int(r.source_record_position), r.event_type, str(r.event_date.date()))
        for r in event_rows
    ]
    zero = [r for r in response["items"] if r["original_fields"]["symbol"] == "ZERO"]
    assert {r["event_type"] for r in zero} == {"ADD", "REMOVE"}
    assert all(r["created_after_event"] for r in zero)
    assert (
        data(client, "/indices/sample/events?start=2026-01-01&end=2026-01-01")["items"]
        == []
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "path,status",
    [
        ("/indices/missing/membership?as_of=2026-01-01", 404),
        ("/securities/v1-s999999", 404),
        ("/securities/TICKER", 422),
        ("/securities/v1-s00", 422),
        ("/securities/v1-s01", 422),
        ("/securities/v01-s0", 422),
        ("/indices/sample/membership?as_of=2025-12-31", 422),
        ("/indices/sample/membership?as_of=2026-02-30", 422),
        ("/indices/sample/membership", 422),
        ("/indices/sample/membership?as_of=1767225600", 422),
        ("/indices/sample/membership?as_of=20260101", 422),
        ("/indices/sample/membership?as_of=2026-01-01T00:00:00", 422),
        ("/indices/sample/events?start=2026-01-20&end=2026-01-01", 422),
        ("/indices/sample/events?start=2025-01-01&end=2026-12-31", 422),
        ("/indices?limit=201", 422),
        ("/indices?limit=0", 422),
        ("/indices?offset=-1", 422),
        ("/indices?offset=1000001", 422),
        ("/rankings?review_id=unknown&reference_date=2026-01-01", 404),
        ("/research/runs/unknown", 404),
        ("/research/runs/saved/records?section=arbitrary", 422),
        ("/research/runs/%2E%2E%2Freport.json", 404),
    ],
)
def test_request_errors(client: TestClient, path: str, status: int) -> None:
    assert client.get(PREFIX + path).status_code == status


@pytest.mark.postgres
def test_valid_empty_membership(database: Engine, tmp_path: Path) -> None:
    fixture = tmp_path / "empty_at_date.csv"
    fixture.write_text(
        "symbol,cik,name,sector,date_added,date_removed,created_at\n"
        "LATER,0001,Later,Tech,2026-01-20,,2026-01-20\n"
    )
    loaded = load(database, fixture)
    with TestClient(
        create_app(configuration(tmp_path, loaded.version_id), database)
    ) as http:
        assert data(http, "/indices/sample/membership?as_of=2026-01-01")["items"] == []


@pytest.mark.postgres
def test_prices_features_ranking_and_unavailable(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = setup(database, tmp_path)
    with Session(database) as session:
        version_id = session.scalars(select(SourceVersion.id)).one()
        expected = query_features(session, Review.from_dict(raw), DAY, membership=True)
    config = configuration(tmp_path, version_id, raw)
    with TestClient(create_app(config, database)) as http:
        lookups = []
        original = Repository.version

        def version(repository: Repository, key: int) -> SourceVersion | None:
            lookups.append(key)
            return original(repository, key)

        with monkeypatch.context() as patch:
            patch.setattr(Repository, "version", version)
            assert data(http, "/reviews")["total"] == 1
        assert lookups == [version_id]
        rows = data(http, "/indices/sample/securities")["items"]
        security_id = next(row["security_id"] for row in rows if row["symbol"] == "A")
        feature = data(
            http,
            f"/securities/{security_id}/features?review_id=sample&reference_date={DAY}",
        )
        assert feature["metadata"]["data_kind"] == "synthetic"
        assert feature["metadata"]["feature_version"] == "observed-20-v2"
        assert feature["result"]["features"] == expected["results"][0]["features"]
        assert len(feature["result"]["inputs"]) == 21
        assert feature["result"]["inputs"][0]["provenance"][0]["source_sha256"]
        ranking = data(
            http,
            f"/rankings?review_id=sample&reference_date={DAY}&membership=true&limit=1",
        )
        assert ranking["total"] == 2 and ranking["items"][0]["rank"] == 1
        assert ranking["items"][0]["security_id"] == security_id
        assert ranking["metadata"]["ranking_complete"]
        assert (
            "not an inclusion/removal probability" in ranking["metadata"]["description"]
        )
        prices = data(
            http,
            f"/securities/{security_id}/prices?review_id=sample&start={DAY}&end={DAY}",
        )
        assert len(prices["items"]) == 1
        assert prices["items"][0]["attributed_to_security"]
        assert Decimal(prices["items"][0]["values"]["close"]) == 110
        empty = data(
            http,
            f"/securities/{security_id}/prices?review_id=sample&start=2025-01-01&end=2025-01-02",
        )
        assert empty["items"] == [] and empty["metadata"]["empty_reason"]
        unavailable = data(
            http,
            f"/securities/{security_id}/features?review_id=sample&reference_date=2026-09-22",
        )
        assert unavailable["result"]["features"]["return_20"] is None
        assert (
            "missing_reference_observation"
            in unavailable["result"]["unavailable"]["return_20"]
        )
        assert (
            http.get(
                PREFIX
                + "/rankings?review_id=sample&reference_date=2026-08-01&membership=true"
            ).status_code
            == 422
        )


@pytest.mark.postgres
@pytest.mark.parametrize("gate", ["mapping", "basis", "source", "dates", "kind"])
def test_review_gates_reach_api(database: Engine, tmp_path: Path, gate: str) -> None:
    raw = setup(database, tmp_path)
    if gate == "mapping":
        raw["universe"][0]["status"] = "unresolved"
    elif gate == "basis":
        raw["basis_review"]["status"] = "unresolved"
    elif gate == "source":
        raw["universe"][0]["sources"][0]["source_sha256"] = "0" * 64
    elif gate == "dates":
        raw["universe"][0]["start"] = "2026-09-02"
    else:
        raw["data_kind"] = "real"
    with Session(database) as session:
        version_id = session.scalars(select(SourceVersion.id)).one()
    with TestClient(
        create_app(configuration(tmp_path, version_id, raw), database)
    ) as http:
        security_id = data(http, "/indices/sample/securities")["items"][0][
            "security_id"
        ]
        url = (
            f"/securities/{security_id}/features?review_id=sample&reference_date={DAY}"
        )
        if gate == "kind":
            assert http.get(PREFIX + url).status_code == 503
        else:
            result = data(http, url)["result"]
            assert result["features"]["return_20"] is None
            assert result["unavailable"]["return_20"]
            if gate == "dates":
                assert result["features"]["average_volume_20"] is not None


@pytest.mark.postgres
def test_saved_research_results_are_reused(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = research_setup(database, tmp_path)
    _, report = research_run(database, bundle)
    report.update(
        review=bundle[1],
        manifest=bundle[0],
        review_sha256=Review.from_dict(bundle[1]).sha256,
    )
    # Required nested fields fail during startup, before requests can consume them.
    source = next(iter(report["source_catalog"].values()))
    for field in ("source_sha256", "metadata_sha256", "counts"):
        broken = dict(
            report,
            source_catalog={"1": {k: v for k, v in source.items() if k != field}},
        )
        path = configuration(tmp_path, report["source_version_id"], report=broken)
        with pytest.raises(ValueError, match=field):
            create_app(path, database)
    broken = dict(report, input_files={"review": {}})
    path = configuration(tmp_path, report["source_version_id"], report=broken)
    with pytest.raises(ValueError, match="sha256"):
        create_app(path, database)
    path = configuration(tmp_path, report["source_version_id"], bundle[1], report)
    with TestClient(create_app(path, database)) as http:
        # An application instance owns one saved snapshot, even if the file changes.
        (tmp_path / "report.json").write_text("broken after startup")

        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("Request must not run training")

        monkeypatch.setattr("indexlens.research.workflow.run_research", forbidden)
        listing = data(http, "/research/runs")["items"][0]
        assert listing["data_kind"] == "synthetic" and listing["run_id"] == "saved"
        summary = data(http, "/research/runs/saved")
        assert (
            summary["result"]["evaluation"]["baseline"]
            == report["evaluation"]["baseline"]
        )
        assert (
            summary["result"]["evaluation"]["logistic"]
            == report["evaluation"]["logistic"]
        )
        assert "predictions" not in summary["result"]["evaluation"]
        assert (
            "Predictive validity remains unestablished."
            in summary["result"]["limitations"]
        )
        for section in (
            "predictions",
            "identities",
            "sources",
            "snapshot_issues",
            "excluded_positives",
            "excluded_reference_dates",
        ):
            result = data(
                http, f"/research/runs/saved/records?section={section}&limit=1"
            )
            assert (
                len(result["items"]) <= 1
                and result["metadata"]["data_kind"] == "synthetic"
            )
        predictions = data(http, "/research/runs/saved/records?section=predictions")
        assert len(predictions["items"]) == len(report["evaluation"]["predictions"])
        assert all(r["security_id"] for r in predictions["items"])

        from sqlalchemy.exc import OperationalError

        def offline(*args: Any, **kwargs: Any) -> None:
            raise OperationalError("offline", {}, Exception("database unavailable"))

        monkeypatch.setattr(database, "connect", offline)
        assert data(http, "/research/runs")["items"][0] == listing
        assert data(http, "/research/runs/saved") == summary
        assert http.get(PREFIX + "/research/runs/unknown").status_code == 404
        assert http.get(PREFIX + "/health").status_code == 503


def test_file_and_response_byte_bounds(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_text('{"large": "1234567890"}')
    with pytest.raises(ValueError, match="size"):
        read_json(path, 10)
    with pytest.raises(ApiError, match="Reduce"):
        bounded({"items": ["x" * (2 * 1024 * 1024)]})


@pytest.mark.postgres
def test_database_errors_are_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError(
            "secret connection SQL", {}, Exception("password=secret")
        )

    monkeypatch.setattr("indexlens.api.repository.Repository.ready", fail)
    response = client.get(PREFIX + "/health")
    assert response.status_code == 503
    assert "secret" not in response.text


@pytest.mark.postgres
def test_config_paths_and_unknown_ids(database: Engine, tmp_path: Path) -> None:
    path = configuration(tmp_path, 1)
    with TestClient(create_app(path, database)) as http:
        assert http.get(PREFIX + "/indices").status_code == 503
        assert http.get(PREFIX + "/research/runs/not-a-path").status_code == 404
    raw = json.loads(path.read_text())
    raw["runs"]["../escape"] = "report.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        create_app(path, database)


@pytest.mark.postgres
def test_price_review_and_alias_date_boundaries(
    database: Engine, tmp_path: Path
) -> None:
    _, review, _ = research_setup(database, tmp_path, historical_alias=True)
    with Session(database) as session:
        version_id = session.scalars(select(SourceVersion.id)).one()
    with TestClient(
        create_app(configuration(tmp_path, version_id, review), database)
    ) as http:
        identities = data(http, "/indices/sample/securities")["items"]
        old = next(r["security_id"] for r in identities if r["symbol"] == "OLD")
        owner = next(r["security_id"] for r in identities if r["symbol"] == "A")
        assert owner != old
        for day, attributed in (("2025-10-14", True), ("2025-10-15", False)):
            response = data(
                http, f"/securities/{old}/prices?review_id=sample&start={day}&end={day}"
            )
            assert response["metadata"]["provider_symbol"] == "A"
            assert response["metadata"]["source_record_positions"] == [0]
            assert response["items"][0]["attributed_to_security"] == attributed
            if not attributed:
                assert "outside_mapping_dates" in response["items"][0]["unavailable"]


@pytest.mark.postgres
@pytest.mark.parametrize("same_hash", [True, False])
def test_security_is_scoped_to_source_version(
    database: Engine, tmp_path: Path, same_hash: bool
) -> None:
    raw = setup(database, tmp_path)
    if not same_hash:
        members = tmp_path / "members.csv"
        members.write_text(members.read_text().replace("0001", "9999"))
    other = load(database, tmp_path / "members.csv", source="second-version")
    with Session(database) as session:
        first_id = session.scalars(
            select(SourceVersion.id).order_by(SourceVersion.id)
        ).first()
    assert first_id is not None
    path = configuration(tmp_path, first_id, raw)
    config = json.loads(path.read_text())
    config["indices"]["other"] = dict(
        version_id=other.version_id, label="Other synthetic"
    )
    path.write_text(json.dumps(config))
    with TestClient(create_app(path, database)) as http:
        first = data(http, "/indices/sample/securities")["items"][0]
        second = data(http, "/indices/other/securities")["items"][0]
        assert first["symbol"] == second["symbol"]
        assert first["security_id"] != second["security_id"]
        # Ambiguous published review versions fail closed, not ticker-joined.
        response = http.get(
            PREFIX
            + f"/securities/{second['security_id']}/features"
            + f"?review_id=sample&reference_date={DAY}"
        )
        assert response.status_code == (503 if same_hash else 422)


@pytest.mark.postgres
def test_unavailable_saved_result_retains_reasons(
    database: Engine, tmp_path: Path
) -> None:
    bundle = research_setup(database, tmp_path)
    _, report = research_run(database, bundle)
    report["review"] = bundle[1]
    report["evaluation"].update(
        status="unavailable",
        baseline=None,
        logistic=None,
        predictions=[],
        unmet_requirements=["no_eligible_training_observations"],
    )
    with TestClient(
        create_app(
            configuration(tmp_path, report["source_version_id"], report=report),
            database,
        )
    ) as http:
        result = data(http, "/research/runs/saved")["result"]["evaluation"]
        assert result["baseline"] is None and result["logistic"] is None
        assert result["unmet_requirements"] == ["no_eligible_training_observations"]
        assert (
            data(http, "/research/runs/saved/records?section=predictions")["items"]
            == []
        )


@pytest.mark.postgres
def test_read_only_requests_and_http_byte_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    def write(repository: Repository) -> None:
        repository.session.execute(text("DELETE FROM source_versions"))

    with monkeypatch.context() as patch:
        patch.setattr(Repository, "ready", write)
        assert client.get(PREFIX + "/health").status_code == 503
    assert data(client, "/indices")["total"] == 1
    monkeypatch.setattr(
        "indexlens.api.service.Service.indices",
        lambda *args: dict(items=["x" * (2 * 1024 * 1024)]),
    )
    response = client.get(PREFIX + "/indices")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "response_too_large"


@pytest.mark.parametrize("report", [{}, {"data_kind": "made-up"}])
def test_malformed_saved_report_fails_at_startup(
    tmp_path: Path, report: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        create_app(configuration(tmp_path, 1, report=report))


@pytest.mark.postgres
@pytest.mark.parametrize("endpoint", ["prices", "features", "rankings"])
def test_missing_price_dataset_is_unavailable(
    database: Engine, tmp_path: Path, endpoint: str
) -> None:
    raw = setup(database, tmp_path)
    raw["dataset_id"] = "missing-synthetic-dataset"
    with Session(database) as session:
        version_id = session.scalars(select(SourceVersion.id)).one()
    with TestClient(
        create_app(configuration(tmp_path, version_id, raw), database)
    ) as http:
        security_id = data(http, "/indices/sample/securities")["items"][0][
            "security_id"
        ]
        paths = {
            "prices": (
                f"/securities/{security_id}/prices"
                f"?review_id=sample&start={DAY}&end={DAY}"
            ),
            "features": (
                f"/securities/{security_id}/features"
                f"?review_id=sample&reference_date={DAY}"
            ),
            "rankings": f"/rankings?review_id=sample&reference_date={DAY}",
        }
        response = http.get(PREFIX + paths[endpoint])
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "evidence_unavailable"


@pytest.mark.postgres
def test_unexpected_value_error_is_not_reclassified(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Unexpected conversion failure")

    monkeypatch.setattr("indexlens.api.service.Service.indices", fail)
    response = client.get(PREFIX + "/indices")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Request failed."}
    }
    assert "Unexpected conversion failure" not in response.text
