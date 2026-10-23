"""Tests for saved cutoff reports across API results and explanation evidence."""

import gzip
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from indexlens.api import openai_provider
from indexlens.api.app import create_app
from indexlens.research import model
from indexlens.research.__main__ import main as research_main
from tests.test_api import PREFIX, data
from tests.test_explanations import MockProvider, draft_for, provider_response
from tests.test_research_database import run, setup


@pytest.mark.postgres
def test_saved_cutoffs_survive_selection_records_and_explanations(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = setup(database, tmp_path)
    # Fit only the small synthetic source report, then invoke the public command.
    rows, original = run(database, bundle, 0.5)
    observations = tmp_path / "observations.jsonl.gz"
    with gzip.open(observations, "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    original.update(
        review=bundle[1],
        manifest=bundle[0],
        observation_artifact=dict(
            file=observations.name,
            rows=len(rows),
            sha256=sha256(observations.read_bytes()).hexdigest(),
        ),
    )
    source_id = "sample-cutoff-0p5"
    source_path = tmp_path / (source_id + ".json")
    source_path.write_text(json.dumps(original))
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    output = tmp_path / "derived"
    assert (
        research_main(
            [
                "evaluate-cutoffs",
                "--source",
                str(source_path),
                "--source-run-id",
                source_id,
                "--source-sha256",
                source_hash,
                "--cutoffs",
                "0.05",
                "0.005",
                "0.002",
                "0.0016",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    reports = {source_id: original}
    filenames = {source_id: source_path.name}
    for cutoff in (0.05, 0.005, 0.002, 0.0016):
        label = str(cutoff).replace(".", "p")
        run_id = "sample-cutoff-" + label
        filename = "derived/cutoff_" + label + ".json"
        reports[run_id] = json.loads((tmp_path / filename).read_text())
        filenames[run_id] = filename
    hashes = {
        run_id: sha256((tmp_path / filenames[run_id]).read_bytes()).hexdigest()
        for run_id in reports
    }
    assert hashes[source_id] == source_hash
    config = dict(
        indices={
            "sample": {
                "version_id": original["source_version_id"],
                "label": "Synthetic fixture",
            }
        },
        runs={"old-saved": source_path.name},
    )
    path = tmp_path / "api.json"
    path.write_text(json.dumps(config))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Saved results must not fit or recalculate metrics")

    monkeypatch.setattr(model, "metrics", forbidden)
    monkeypatch.setattr(model, "compare", forbidden)
    monkeypatch.setattr("indexlens.research.workflow.run_research", forbidden)
    mock = MockProvider()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key-never-live")
    monkeypatch.setenv("INDEXLENS_OPENAI_MODEL", "test-model")
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)

    def success(body: dict[str, Any]) -> httpx.Response:
        evidence = json.loads(body["input"][0]["content"])["evidence"]
        references = [
            "/result/evaluation/threshold",
            "/result/evaluation/logistic/metrics/precision",
            "/result/evaluation/logistic/metrics/recall",
            "/result/limitations/0",
        ]
        if "derivation" in evidence["result"]["evaluation"]:
            references += [
                "/result/evaluation/derivation/source_run_id",
                "/result/evaluation/derivation/exploratory",
            ]
        return httpx.Response(
            200, json=provider_response(draft_for(evidence, references))
        )

    mock.handler = success
    with TestClient(create_app(path, database)) as previous:
        config["runs"] = filenames
        path.write_text(json.dumps(config))
        assert data(previous, "/research/runs")["items"][0]["run_id"] == "old-saved"
        assert previous.get(PREFIX + "/research/runs/" + source_id).status_code == 404

    with TestClient(create_app(path, database)) as client:
        listing = data(client, "/research/runs")["items"]
        assert {row["run_id"] for row in listing} == set(reports)
        assert not mock.requests
        assert {row["threshold"] for row in listing} == {
            0.5,
            0.05,
            0.005,
            0.002,
            0.0016,
        }
        for row in listing:
            assert row["data_kind"] == "synthetic"
            assert row["report_sha256"] == hashes[row["run_id"]]
        for run_id, report in reports.items():
            route = "/research/runs/" + run_id
            summary = data(client, route)
            assert summary["result"]["evaluation"] == {
                k: v for k, v in report["evaluation"].items() if k != "predictions"
            }
            assert summary["result"]["limitations"][:-1] == report["limitations"]
            rows = []
            offset = 0
            while True:
                page = data(
                    client,
                    route + f"/records?section=predictions&limit=1&offset={offset}",
                )
                assert page["metadata"]["report_sha256"] == hashes[run_id]
                assert page["metadata"]["data_kind"] == "synthetic"
                assert all(row["security_id"] for row in page["items"])
                rows += [
                    {k: v for k, v in row.items() if k != "security_id"}
                    for row in page["items"]
                ]
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            assert rows == original["evaluation"]["predictions"]
            response = client.post(
                PREFIX + "/research/explain",
                json={"target": {"kind": "run", "run_id": run_id}},
            )
            assert response.status_code == 200, response.text
            explained = response.json()
            assert explained["evidence"]["result"] == summary["result"]
            assert explained["evidence"]["metadata"] == summary["metadata"]
            assert explained["claims"][0]["value_json"] == json.dumps(
                report["evaluation"]["threshold"]
            )
        calls = len(mock.requests)
        assert client.get(PREFIX + "/research/runs/old-saved").status_code == 404
        assert (
            client.post(
                PREFIX + "/research/explain",
                json={"target": {"kind": "run", "run_id": "old-saved"}},
            ).status_code
            == 404
        )
        assert len(mock.requests) == calls

        def wrong_cutoff(body: dict[str, Any]) -> httpx.Response:
            evidence = json.loads(body["input"][0]["content"])["evidence"]
            draft = draft_for(evidence, ["/result/evaluation/threshold"])
            draft.claims[0].value_json = "0.05"
            return httpx.Response(200, json=provider_response(draft))

        mock.handler = wrong_cutoff
        response = client.post(
            PREFIX + "/research/explain",
            json={"target": {"kind": "run", "run_id": source_id}},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_claim"
    assert sha256(source_path.read_bytes()).hexdigest() == source_hash
