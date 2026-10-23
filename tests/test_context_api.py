"""Tests for benchmark financial context API alignment and missing datasets."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from indexlens.api.app import create_app
from indexlens.db.models import SourceVersion
from tests.test_api import configuration, data
from tests.test_feature_database import DAY, setup


@pytest.mark.postgres
def test_benchmark_review_alignment_and_missing_dataset(
    database: Engine, tmp_path: Path
) -> None:
    from indexlens.db.models import PriceSource
    from indexlens.prices.loading import load_price_csv
    from tests.test_price_database import metadata

    raw = setup(database, tmp_path)
    raw["financial_context"] = dict(currency="USD", return_convention="price_return")
    lines = (tmp_path / "prices.csv").read_text().splitlines()
    path = tmp_path / "benchmark.csv"
    path.write_text(
        lines[0]
        + "\n"
        + "\n".join(
            line.replace("A,", "^GSPC,", 1)
            for line in lines[1:]
            if line.startswith("A,")
        )
        + "\n"
    )
    loaded = load_price_csv(
        database, path, metadata(dataset_id="benchmark", provider="fixture")
    )
    with Session(database) as session:
        source = session.get(PriceSource, loaded.source_id)
        assert source is not None
        pin = dict(
            source_sha256=source.source_sha256, metadata_sha256=source.metadata_sha256
        )
        version = session.scalars(select(SourceVersion.id)).one()
    benchmark = {
        k: raw[k]
        for k in (
            "reviewed_on",
            "data_kind",
            "provider",
            "price_basis",
            "adjustment_settings",
            "limitations",
            "basis_review",
        )
    }
    benchmark.update(
        review_version="benchmark-review-v1",
        review_id="market",
        dataset_id="benchmark",
        currency="USD",
        return_convention="price_return",
        benchmark=dict(
            raw["basis_review"],
            provider_symbol="^GSPC",
            instrument_type="INDEX",
            sources=[pin],
        ),
    )
    (tmp_path / "market.json").write_text(json.dumps(benchmark))
    config = configuration(tmp_path, version, raw)
    settings = json.loads(config.read_text())
    settings["benchmarks"] = {"market": "market.json"}
    config.write_text(json.dumps(settings))
    with TestClient(create_app(config, database)) as client:
        security = data(client, "/indices/sample/securities")["items"][0]["security_id"]
        query = (
            f"/securities/{security}/financial-context?review_id=sample"
            f"&reference_date={DAY}&benchmark_id=market"
        )
        result = data(client, query)
        assert result["result"]["indicators"]["excess_return_20"]["value"] == "0.0"
        assert result["result"]["indicators"]["relative_volatility_20"]["value"] == "1"
        assert result["metadata"]["benchmark"]["identity"]["provider_symbol"] == "^GSPC"
    benchmark["dataset_id"] = "absent"
    (tmp_path / "market.json").write_text(json.dumps(benchmark))
    with TestClient(create_app(config, database)) as client:
        result = data(client, query)
        assert result["result"]["indicators"]["excess_return_20"]["value"] is None
        assert result["result"]["indicators"]["return_20"]["value"] == "0.1"
