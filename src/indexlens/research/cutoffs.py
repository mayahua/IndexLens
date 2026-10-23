"""Evaluate classification cutoffs using a saved report's probabilities."""

import argparse
import gzip
import importlib.metadata
import io
import json
import math
import os
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .model import NINE_RESEARCH_VERSION, cutoff_limitation, metrics, validate_cutoff
from .provenance import code_state

MODELS = ("baseline", "logistic")
IDENTITY = ("symbol", "cik", "reference_date")


def model_names(report: dict[str, Any]) -> tuple[str, ...]:
    return MODELS + (
        ("matched_three",)
        if report.get("research_version") == NINE_RESEARCH_VERSION
        else ()
    )


def count_matches(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def same_metric(value: Any, expected: Any) -> bool:
    if type(value) in (float, int) and type(expected) in (float, int):
        return math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-15)
    return bool(value == expected)


def validate_source(report: dict[str, Any], source: Path) -> Path:
    """Check saved metrics and alignment against the hashed observation artifact."""
    try:
        evaluation = report["evaluation"]
        threshold = validate_cutoff(evaluation["threshold"])
        predictions = evaluation["predictions"]
        if (
            evaluation["status"] not in ("evaluated", "evaluation_single_class")
            or evaluation["unmet_requirements"]
            or not isinstance(predictions, list)
            or not predictions
            or report["data_kind"] not in ("real", "synthetic")
        ):
            raise ValueError("Source needs complete saved predictions for both models.")
        identities = [tuple(row[key] for key in IDENTITY) for row in predictions]
        if any(not isinstance(v, str) or not v for row in identities for v in row):
            raise ValueError(
                "Prediction identities and dates must be nonempty strings."
            )
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate prediction identities/dates.")
        labels = [row["label"] for row in predictions]
        for name in model_names(report):
            probabilities = [row[name + "_probability"] for row in predictions]
            expected = metrics(labels, probabilities, threshold)
            saved = evaluation[name]["metrics"]
            for key, value in expected.items():
                # Historical metrics predate the explicit alert count.
                if (
                    key
                    in {
                        "predicted_removals",
                        "false_alarms",
                        "true_positives",
                        "false_negatives",
                    }
                    and key not in saved
                ):
                    continue
                if key not in saved or not same_metric(saved[key], value):
                    raise ValueError(
                        f"Source {name} metric {key} disagrees with predictions."
                    )
        expected_status = (
            "evaluated" if len(set(labels)) == 2 else "evaluation_single_class"
        )
        if evaluation["status"] != expected_status:
            raise ValueError("Evaluation status disagrees with saved label classes.")
        frequency = validate_cutoff(evaluation["baseline"]["probability"])
        if any(row["baseline_probability"] != frequency for row in predictions):
            raise ValueError("Baseline predictions differ from the fitted frequency.")
        summary = report["summaries"]["test"]["eligible"]
        for value, expected_count in (
            (evaluation["test_observations"], len(predictions)),
            (summary["observations"], len(predictions)),
            (summary["positive_observations"], sum(labels)),
            (summary["negative_observations"], len(labels) - sum(labels)),
        ):
            if not count_matches(value, expected_count):
                raise ValueError(
                    "Source prediction counts disagree with evaluation/summary."
                )
        artifact = report["observation_artifact"]
        observation_path = (source.parent / Path(artifact["file"])).resolve()
        content = observation_path.read_bytes()
        if sha256(content).hexdigest() != artifact["sha256"]:
            raise ValueError("Observation artifact SHA-256 mismatch.")
        aligned = []
        training_labels = []
        row_count = 0
        with gzip.open(io.BytesIO(content), "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                row_count += 1
                if row["eligible"] is True and row["split"] == "train":
                    training_labels.append(row["label_after_source_screen"])
                if row["eligible"] is True and row["split"] == "test":
                    label = row["label_after_source_screen"]
                    if type(label) is not int or label not in (0, 1):
                        raise ValueError("Invalid saved observation label.")
                    aligned.append(tuple(row[k] for k in IDENTITY) + (label,))
                    if report.get("research_version") == NINE_RESEARCH_VERSION:
                        prediction = predictions[len(aligned) - 1]
                        if any(
                            prediction.get(key) != row.get(key)
                            for key in (
                                "feature_observation_date",
                                "context_input_sha256",
                                "source_record_positions",
                            )
                        ) or prediction.get("fitted_values") != {
                            f: row["features"][f] for f in evaluation["fitted_features"]
                        }:
                            raise ValueError(
                                "Saved prediction inputs differ from observations."
                            )
        if report.get("research_version") == NINE_RESEARCH_VERSION and (
            not training_labels
            or any(type(y) is not int or y not in (0, 1) for y in training_labels)
            or not count_matches(
                evaluation["training_observations"], len(training_labels)
            )
            or frequency != sum(training_labels) / len(training_labels)
        ):
            raise ValueError("Baseline must equal eligible training prevalence.")
        if not count_matches(artifact["rows"], row_count):
            raise ValueError("Observation artifact row count mismatch.")
        if aligned != [
            identity + (y,) for identity, y in zip(identities, labels, strict=True)
        ]:
            raise ValueError(
                "Prediction rows/labels do not align with saved observations."
            )
        if not isinstance(report["limitations"], list) or any(
            not isinstance(item, str) for item in report["limitations"]
        ):
            raise ValueError("Source limitations must be a list of strings.")
        return observation_path
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"Incomplete or malformed source report: {exc}.") from exc


def evaluate_cutoffs(
    source: Path,
    cutoffs: list[float],
    output: Path,
    source_run_id: str,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Save separate derived reports in a fresh directory, retaining source evidence."""
    selected = [validate_cutoff(value) for value in cutoffs]
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Supply one or more distinct cutoffs.")
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", source_run_id) is None:
        raise ValueError("Source run ID must be a valid API identifier.")
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a new output directory to preserve earlier runs.")
    source_bytes = source.read_bytes()
    digest = sha256(source_bytes).hexdigest()
    if source_sha256 is not None and source_sha256 != digest:
        raise ValueError("Source report SHA-256 mismatch.")
    original = json.loads(source_bytes)
    if not isinstance(original, dict):
        raise ValueError("Source report must contain a JSON object.")
    observation_path = validate_source(original, source)
    # Also reject nonfinite values elsewhere in the saved report before writing.
    json.dumps(original, allow_nan=False)
    created_at = datetime.now(timezone.utc).isoformat()
    code = code_state(Path(__file__).resolve().parents[3])
    evaluation_code = dict(
        code=code,
        package_versions={
            name: importlib.metadata.version(name)
            for name in ("numpy", "scikit-learn", "scipy")
        },
    )
    generated = {}
    for cutoff in selected:
        report = deepcopy(original)
        evaluation = report["evaluation"]
        evaluation["threshold"] = cutoff
        predictions = evaluation["predictions"]
        for name in model_names(report):
            evaluation[name]["metrics"] = metrics(
                [row["label"] for row in predictions],
                [row[name + "_probability"] for row in predictions],
                cutoff,
            )
        derivation = dict(
            version="saved-cutoff-v1",
            kind="saved_probability_cutoff_evaluation",
            source_run_id=source_run_id,
            source_report=os.path.relpath(source, output),
            source_report_sha256=digest,
            source_created_at=original.get("created_at"),
            source_threshold=original["evaluation"]["threshold"],
            selected_threshold=cutoff,
            classification="probability >= threshold",
            evaluated_at=created_at,
            exploratory=True,
            previously_inspected_test_data=True,
            **evaluation_code,
        )
        if "derivation" in original["evaluation"]:
            # The source hash/reference retains the complete derivation chain.
            derivation["source_derivation"] = original["evaluation"]["derivation"]
        evaluation["derivation"] = derivation
        report["created_at"] = created_at
        report["observation_artifact"]["file"] = os.path.relpath(
            observation_path, output
        )
        # Keep historical wording explicitly attributed, including prior cutoffs.
        report["limitations"] = [
            "Source report limitation: " + item for item in original["limitations"]
        ] + [
            cutoff_limitation(cutoff),
            "This exploratory evaluation reuses saved labels and probabilities. "
            "The fitted model and its configuration are retained from the source. "
            "Comparisons on inspected test data are not independent validation "
            "and do not establish an optimal cutoff or predictive usefulness.",
        ]
        filename = "cutoff_" + str(cutoff).replace(".", "p") + ".json"
        generated[filename] = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if code_state(Path(__file__).resolve().parents[3]) != code:
        raise ValueError("Code changed during evaluation; retry stable code.")
    if source.read_bytes() != source_bytes:
        raise ValueError(
            "Source report changed during evaluation; retry a stable source."
        )
    if (
        sha256(observation_path.read_bytes()).hexdigest()
        != original["observation_artifact"]["sha256"]
    ):
        raise ValueError("Observation artifact changed during evaluation.")
    output.mkdir(parents=True)
    entries = []
    for cutoff, (filename, content) in zip(selected, generated.items(), strict=True):
        with (output / filename).open("x", encoding="utf-8") as stream:
            stream.write(content)
        entries.append(
            dict(
                threshold=cutoff,
                file=filename,
                sha256=sha256(content.encode()).hexdigest(),
            )
        )
    return dict(output=str(output), source_report_sha256=digest, reports=entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument(
        "--source-sha256", help="Expected SHA-256 of source report bytes."
    )
    parser.add_argument("--cutoffs", type=float, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate_cutoffs(
            args.source,
            args.cutoffs,
            args.output,
            args.source_run_id,
            args.source_sha256,
        )
    except (OSError, ValueError, EOFError) as exc:
        print(f"Cutoff evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
