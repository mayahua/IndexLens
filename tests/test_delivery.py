"""Tests for deterministic sample delivery, repeat loading and safe request logs."""

import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from indexlens.api.app import create_app
from indexlens.api.logging import request_summary
from scripts.sample import load, prepare
from tests.conftest import ROOT


def test_sample_inputs_are_deterministic_and_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first, second = Path("data/first"), Path("data/second")
    prepare(first)
    prepare(second)
    for name in (
        "membership.csv",
        "prices.csv",
        "prices.metadata.json",
        "manifest.json",
        "review.json",
        "ingestion.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    with pytest.raises(FileExistsError):
        prepare(first)
    with pytest.raises(ValueError, match="under data"):
        prepare(Path("outside"))


def test_request_logs_redact_paths_queries_bodies_and_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.middleware("http")(request_summary)

    @app.post("/items/{item_id}")
    def fail(item_id: str) -> None:
        raise ValueError("postgresql://private-user:secret@example/db")

    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error"),
        TestClient(app) as client,
    ):
        response = client.post(
            "/items/private-id?token=secret",
            json={"question": "private-payload"},
            headers={"Authorization": "Bearer private-key"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert client.get("/private-unmatched?token=secret").status_code == 404
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "uvicorn.error"
    ]
    assert [r["route"] for r in records] == ["/items/{item_id}", "unmatched"]
    assert [r["status"] for r in records] == [500, 404]
    assert all(r["elapsed_ms"] >= 0 for r in records)
    for secret in ("private", "secret", "postgresql", "Authorization"):
        assert secret not in json.dumps(records)
        assert secret not in response.text


@pytest.mark.postgres
def test_public_sample_commands_repeat_with_equivalent_results(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    monkeypatch.setenv(
        "INDEXLENS_DATABASE_URL", database.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reports = []
    versions = []
    for name in ("first", "second"):
        output = Path("data") / name
        load(output)
        reports.append(json.loads((output / "run/report.json").read_text()))
        config = json.loads((output / "api.json").read_text())
        versions.append(config["indices"]["sample"]["version_id"])
    assert versions[0] == versions[1]
    assert reports[0]["evaluation"] == reports[1]["evaluation"]
    assert reports[0]["observation_artifact"] == reports[1]["observation_artifact"]
    assert reports[0]["evaluation"]["status"] == "evaluated"
    for split in ("train", "test"):
        counts = reports[0]["summaries"][split]["eligible"]
        assert counts["observations"] == 4 and counts["positive_observations"] == 1
    with TestClient(create_app(Path("data/first/api.json"), database)) as client:
        response = client.get("/api/v1/research/runs/sample-run")
        assert response.status_code == 200
        assert response.json()["metadata"]["data_kind"] == "synthetic"
        response = client.post(
            "/api/v1/research/explain",
            json={
                "target": {"kind": "run", "run_id": "sample-run"},
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "openai_not_configured"
