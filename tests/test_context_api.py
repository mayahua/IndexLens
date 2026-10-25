"""Tests for financial context API evidence and exact explanation claims."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from indexlens.api import openai_provider
from indexlens.api.app import create_app
from indexlens.db.models import SourceVersion
from tests.test_api import PREFIX, configuration, data
from tests.test_explanations import MockProvider, draft_for, provider_response
from tests.test_feature_database import DAY, setup


@pytest.mark.postgres
def test_context_get_and_mock_explanation(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = setup(database, tmp_path)
    raw["financial_context"] = dict(
        currency="USD",
        return_convention="price_return",
        dollar_volume_review=raw["basis_review"],
    )
    with Session(database) as session:
        version = session.scalars(select(SourceVersion.id)).one()
    path = configuration(tmp_path, version, raw)
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key-never-live")
    with TestClient(create_app(path, database)) as client:
        security = data(client, "/indices/sample/securities")["items"][0]["security_id"]
        query = (
            f"/securities/{security}/financial-context"
            f"?review_id=sample&reference_date={DAY}"
        )
        result = data(client, query)
        assert result["metadata"]["data_kind"] == "synthetic"
        assert result["result"]["indicators"]["return_20"]["value"] == "0.1"
        assert result["result"]["indicators"]["return_60"]["value"] is None
        assert (
            result["result"]["indicators"]["average_dollar_volume_20"]["value"]
            is not None
        )
        assert result["result"]["indicators"]["excess_return_20"]["value"] is None
        request = dict(
            target=dict(
                kind="financial_context",
                security_id=security,
                review_id="sample",
                reference_date=str(DAY),
            )
        )
        pointer = "/result/indicators/return_20/value"

        def success(body: dict[str, Any]) -> httpx.Response:
            evidence = json.loads(body["input"][0]["content"])["evidence"]
            assert (
                evidence["result"]["input_sha256"] == result["result"]["input_sha256"]
            )
            assert evidence["result"]["indicators"]["return_20"]["value"] == "0.1"
            assert "inputs" not in evidence["result"]
            assert evidence["interpretation"]["status"] == "limited"
            return httpx.Response(
                200, json=provider_response(draft_for(evidence, [pointer]))
            )

        mock.handler = success
        response = client.post(PREFIX + "/research/explain", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["validation"]["scalar_claims"]

        def fabricated(body: dict[str, Any]) -> httpx.Response:
            evidence = json.loads(body["input"][0]["content"])["evidence"]
            draft = draft_for(evidence, [pointer])
            draft.claims[0].value_json = '"0.9"'
            return httpx.Response(200, json=provider_response(draft))

        mock.handler = fabricated
        assert (
            client.post(PREFIX + "/research/explain", json=request).status_code == 502
        )
        assert client.get(PREFIX + query + "&benchmark_id=unknown").status_code == 404
        assert (
            data(
                client,
                f"/securities/{security}/features?review_id=sample&reference_date={DAY}",
            )["metadata"]["feature_version"]
            == "observed-20-v2"
        )


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
