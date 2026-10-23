"""Tests for cutoff validation, saved evaluation integrity and fitting isolation."""

import gzip
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from indexlens.research import __main__ as cli
from indexlens.research.cutoffs import evaluate_cutoffs
from indexlens.research.model import compare, metrics, validate_cutoff
from tests.test_research import model_rows


@pytest.mark.parametrize(
    "cutoff,matrix",
    [
        (0, [[0, 2], [0, 2]]),
        (0.05, [[1, 1], [0, 2]]),
        (0.5, [[1, 1], [1, 1]]),
        (1, [[2, 0], [1, 1]]),
    ],
)
def test_cutoff_endpoints_and_equality(cutoff: float, matrix: list[list[int]]) -> None:
    result = metrics([0, 1, 0, 1], [0, 0.05, 0.5, 1], cutoff)
    assert result["confusion_matrix"] == matrix
    tn, fp = matrix[0]
    fn, tp = matrix[1]
    assert result["predicted_removals"] == tp + fp
    assert result["precision"] == tp / (tp + fp)
    assert result["recall"] == tp / (tp + fn)
    assert result["f1"] == 2 * tp / (2 * tp + fp + fn)
    assert tn + fp + fn + tp == result["observations"]
    assert result["average_precision"] == pytest.approx(5 / 6)
    assert result["brier_score"] == pytest.approx((0.95**2 + 0.5**2) / 4)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        "0.05",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.1,
        1.1,
        10**1000,
    ],
)
def test_invalid_cutoffs_rejected_everywhere(value: Any, tmp_path: Path) -> None:
    for action in (
        lambda: validate_cutoff(value),
        lambda: metrics([], [], value),
        lambda: compare([], 42, value),
        lambda: evaluate_cutoffs(
            tmp_path / "missing", [value], tmp_path / "out", "sample"
        ),
    ):
        with pytest.raises(ValueError, match="Cutoff"):
            action()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(dict(config_version="removal-run-v2", seed=42, cutoff=value))
    )
    with pytest.raises(ValueError, match="Cutoff"):
        cli.execute(config, Path.cwd() / "data" / tmp_path.name)


@pytest.mark.parametrize(
    "labels,probabilities",
    [
        ([True], [0.1]),
        ([0.0], [0.1]),
        ([2], [0.1]),
        ([0], []),
        ([0], [True]),
        ([0], ["0.1"]),
        ([0], [None]),
        ([0], [float("nan")]),
        ([0], [float("inf")]),
        ([0], [-0.1]),
        ([0], [1.01]),
    ],
)
def test_invalid_metric_inputs(labels: Any, probabilities: Any) -> None:
    with pytest.raises(ValueError):
        metrics(labels, probabilities, 0.5)


def test_cutoffs_preserve_fitted_parameters_and_undefined_reasons() -> None:
    original = compare(model_rows(), 42, 0.5)
    changed = compare(model_rows(), 42, 0.002)
    assert original["predictions"] == changed["predictions"]
    assert original["parameters"] == changed["parameters"]
    assert original["model_version"] == changed["model_version"]
    for name in ("baseline", "logistic"):
        assert {k: v for k, v in original[name].items() if k != "metrics"} == {
            k: v for k, v in changed[name].items() if k != "metrics"
        }
    empty = metrics([], [], 0)
    assert empty["undefined"] == dict(
        precision="zero_denominator",
        recall="zero_denominator",
        f1="zero_denominator",
        average_precision="no_positive_outcomes",
        brier_score="empty_evaluation_set",
    )
    assert metrics([0], [0.5], 1)["limitations"]
    assert metrics([1], [0.5], 1)["precision"] is None


@pytest.fixture
def saved_source(tmp_path: Path) -> Path:
    labels, probabilities = [0, 1, 0, 1], [0, 0.05, 0.5, 1]
    rows = [
        dict(
            symbol=f"S{i}",
            cik=str(i),
            reference_date="2026-06-30",
            label=y,
            baseline_probability=0.25,
            logistic_probability=p,
        )
        for i, (y, p) in enumerate(zip(labels, probabilities, strict=True))
    ]
    observations = tmp_path / "observations.jsonl.gz"
    with gzip.open(observations, "wt") as stream:
        for row in rows:
            stream.write(
                json.dumps(
                    dict(
                        symbol=row["symbol"],
                        cik=row["cik"],
                        reference_date=row["reference_date"],
                        label_after_source_screen=row["label"],
                        eligible=True,
                        split="test",
                    )
                )
                + "\n"
            )
    report: dict[str, Any] = dict(
        data_kind="synthetic",
        created_at="2026-10-23T00:00:00+00:00",
        configuration=dict(config_version="removal-run-v1", seed=42),
        code=dict(revision="synthetic-source", dirty=True),
        limitations=["Synthetic source; no predictive validity established."],
        summaries=dict(
            test=dict(
                eligible=dict(
                    observations=4,
                    positive_observations=2,
                    negative_observations=2,
                )
            )
        ),
        observation_artifact=dict(
            file=observations.name,
            rows=4,
            sha256=sha256(observations.read_bytes()).hexdigest(),
        ),
        evaluation=dict(
            status="evaluated",
            threshold=0.5,
            test_observations=4,
            unmet_requirements=[],
            predictions=rows,
            model_version="scaled-logistic-v1",
            baseline=dict(probability=0.25, metrics=metrics(labels, [0.25] * 4, 0.5)),
            logistic=dict(
                coefficients=[0.1, 0.2, 0.3],
                intercept=0.4,
                scaling=dict(mean=[1, 2, 3], scale=[1, 1, 1]),
                metrics=metrics(labels, probabilities, 0.5),
            ),
        ),
    )
    # Historical reports did not contain this additional metric field.
    for name in ("baseline", "logistic"):
        for field in (
            "predicted_removals",
            "false_alarms",
            "true_positives",
            "false_negatives",
        ):
            del report["evaluation"][name]["metrics"][field]
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    return source


def test_public_saved_command_prohibits_retraining_and_preserves_artifacts(
    saved_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Saved cutoff evaluation must reuse existing results")

    for target in (
        "indexlens.research.model.compare",
        "indexlens.research.workflow.compare",
        "indexlens.research.workflow.run_research",
        "indexlens.research.__main__.run_research",
        "indexlens.research.workflow.build_observations",
        "indexlens.features.core.calculate",
        "indexlens.research.labels.label_snapshot",
        "indexlens.db.session.database_engine",
        "indexlens.research.__main__.database_engine",
        "sqlalchemy.engine.Engine.connect",
        "sklearn.linear_model.LogisticRegression.fit",
        "sklearn.preprocessing.StandardScaler.fit",
        "socket.socket.connect",
    ):
        monkeypatch.setattr(target, forbidden)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    source = json.loads(saved_source.read_text())
    output = tmp_path / "derived"
    assert (
        cli.main(
            [
                "evaluate-cutoffs",
                "--source",
                str(saved_source),
                "--source-run-id",
                "sample",
                "--cutoffs",
                "0",
                "0.05",
                "0.5",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    listing = json.loads(capsys.readouterr().out)
    assert len(listing["reports"]) == 4
    for item in listing["reports"]:
        target = output / item["file"]
        assert sha256(target.read_bytes()).hexdigest() == item["sha256"]
        report = json.loads(target.read_text())
        assert report["configuration"] == source["configuration"]
        assert report["code"] == source["code"]
        assert report["data_kind"] == "synthetic"
        evaluation = report["evaluation"]
        assert evaluation["predictions"] == source["evaluation"]["predictions"]
        derivation = evaluation["derivation"]
        assert (
            derivation["source_report_sha256"]
            == sha256(before[saved_source]).hexdigest()
        )
        assert (output / derivation["source_report"]).resolve() == saved_source
        assert derivation["source_threshold"] == 0.5
        assert (
            derivation["selected_threshold"]
            == evaluation["threshold"]
            == item["threshold"]
        )
        assert derivation["evaluated_at"] and derivation["code"]["file_sha256"]
        assert (
            derivation["exploratory"] and derivation["previously_inspected_test_data"]
        )
        artifact = report["observation_artifact"]
        assert (
            sha256((output / artifact["file"]).read_bytes()).hexdigest()
            == artifact["sha256"]
        )
        for name in ("baseline", "logistic"):
            old = source["evaluation"][name]
            assert {k: v for k, v in evaluation[name].items() if k != "metrics"} == {
                k: v for k, v in old.items() if k != "metrics"
            }
            for metric in ("average_precision", "brier_score"):
                assert evaluation[name]["metrics"][metric] == old["metrics"][metric]
        if item["threshold"] == 0.05:
            actual = evaluation["logistic"]["metrics"]
            assert actual["confusion_matrix"] == [[1, 1], [0, 2]]
            assert actual["precision"] == pytest.approx(2 / 3)
            assert actual["recall"] == 1 and actual["f1"] == 0.8
    assert all(p.read_bytes() == content for p, content in before.items())
    again = tmp_path / "second-generation"
    evaluate_cutoffs(output / "cutoff_0p05.json", [0.002], again, "sample-derived")
    second = json.loads((again / "cutoff_0p002.json").read_text())
    assert (
        second["evaluation"]["derivation"]["source_derivation"]["source_threshold"]
        == 0.5
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_probability",
        "bad_probability",
        "bad_label",
        "duplicate",
        "reorder",
        "label_alignment",
        "test_count",
        "summary_count",
        "row_count",
        "artifact_hash",
        "missing_model",
        "unavailable",
        "empty",
        "metric",
        "threshold",
        "baseline",
    ],
)
def test_malformed_sources_fail_before_output(
    saved_source: Path,
    tmp_path: Path,
    damage: str,
) -> None:
    report = json.loads(saved_source.read_text())
    e = report["evaluation"]
    if damage == "missing_probability":
        del e["predictions"][0]["logistic_probability"]
    elif damage == "bad_probability":
        e["predictions"][0]["logistic_probability"] = True
    elif damage == "bad_label":
        e["predictions"][0]["label"] = 2
    elif damage == "duplicate":
        e["predictions"][1] = deepcopy(e["predictions"][0])
    elif damage == "reorder":
        e["predictions"].reverse()
    elif damage == "label_alignment":
        # Exchange labels/probabilities, keeping aggregate metrics unchanged.
        for key in ("label", "logistic_probability"):
            e["predictions"][0][key], e["predictions"][1][key] = (
                e["predictions"][1][key],
                e["predictions"][0][key],
            )
    elif damage == "test_count":
        e["test_observations"] = 3
    elif damage == "summary_count":
        report["summaries"]["test"]["eligible"]["positive_observations"] = 3
    elif damage == "row_count":
        report["observation_artifact"]["rows"] = 3
    elif damage == "artifact_hash":
        report["observation_artifact"]["sha256"] = "0" * 64
    elif damage == "missing_model":
        e["logistic"] = None
    elif damage == "unavailable":
        e["status"] = "unavailable"
    elif damage == "empty":
        e["predictions"] = []
    elif damage == "metric":
        e["logistic"]["metrics"]["precision"] = 0.99
    elif damage == "threshold":
        e["threshold"] = True
    elif damage == "baseline":
        e["baseline"]["probability"] = 0.1
    saved_source.write_text(json.dumps(report))
    output = tmp_path / "bad"
    with pytest.raises(ValueError):
        evaluate_cutoffs(saved_source, [0.05], output, "sample")
    assert not output.exists()


def test_hash_collisions_and_invalid_batches(
    saved_source: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "derived"
    for cutoffs in ([], [0.05, 0.05], [0.05, float("nan")]):
        with pytest.raises(ValueError):
            evaluate_cutoffs(saved_source, cutoffs, output, "sample")
        assert not output.exists()
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_cutoffs(saved_source, [0.05], output, "sample", "0" * 64)
    assert not output.exists()
    evaluate_cutoffs(saved_source, [0.05], output, "sample")
    before = {p: p.read_bytes() for p in output.iterdir()}
    assert (
        cli.main(
            [
                "evaluate-cutoffs",
                "--source",
                str(saved_source),
                "--source-run-id",
                "sample",
                "--cutoffs",
                "0.05",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert "Choose a new output directory" in capsys.readouterr().err
    assert all(p.read_bytes() == content for p, content in before.items())


def test_saved_single_class_retains_undefined_metrics(
    saved_source: Path, tmp_path: Path
) -> None:
    report = json.loads(saved_source.read_text())
    evaluation = report["evaluation"]
    evaluation["status"] = "evaluation_single_class"
    for row in evaluation["predictions"]:
        row["label"] = 0
    for name in ("baseline", "logistic"):
        evaluation[name]["metrics"] = metrics(
            [0] * 4,
            [row[name + "_probability"] for row in evaluation["predictions"]],
            0.5,
        )
    report["summaries"]["test"]["eligible"].update(
        positive_observations=0, negative_observations=4
    )
    artifact = tmp_path / report["observation_artifact"]["file"]
    with gzip.open(artifact, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    with gzip.open(artifact, "wt") as stream:
        for row in rows:
            row["label_after_source_screen"] = 0
            stream.write(json.dumps(row) + "\n")
    report["observation_artifact"]["sha256"] = sha256(artifact.read_bytes()).hexdigest()
    saved_source.write_text(json.dumps(report))
    output = tmp_path / "single-class"
    evaluate_cutoffs(saved_source, [1], output, "sample")
    saved = json.loads((output / "cutoff_1p0.json").read_text())
    assert saved["evaluation"]["status"] == "evaluation_single_class"
    assert saved["evaluation"]["logistic"]["metrics"]["average_precision"] is None
    assert (
        saved["evaluation"]["logistic"]["metrics"]["undefined"]["recall"]
        == "zero_denominator"
    )
