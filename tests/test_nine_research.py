"""Tests for the frozen nine-feature fit, matched cohort and saved cutoffs."""

import copy
import gzip
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from indexlens.research.cutoffs import evaluate_cutoffs
from indexlens.research.model import (
    ILLUSTRATIVE_CUTOFFS,
    NINE_FEATURES,
    NINE_RESEARCH_VERSION,
    compare_nine,
)


def observations() -> list[dict[str, Any]]:
    return [
        dict(
            symbol=f"S{i}",
            cik=str(i),
            reference_date="2025-07-31" if i < 8 else "2026-07-31",
            feature_observation_date="2025-07-31" if i < 8 else "2026-07-31",
            split="train" if i < 8 else "test",
            eligible=True,
            label_after_source_screen=int(i % 3 == 0),
            features={
                f: str((i + 1) ** (1 + j % 2) / (j + 1))
                for j, f in enumerate((*NINE_FEATURES, "average_volume_20"))
            },
            context_input_sha256="a" * 64,
            source_record_positions=[i],
        )
        for i in range(12)
    ]


def test_training_only_scaling_and_probability_invariance() -> None:
    rows = observations()
    result = compare_nine(rows)
    assert result["fitted_features"] == list(NINE_FEATURES)
    assert result["baseline"]["probability"] == 3 / 8
    for name, features in (
        ("logistic", NINE_FEATURES),
        ("matched_three", result["comparator_features"]),
    ):
        x = [[float(r["features"][f]) for f in features] for r in rows[:8]]
        np.testing.assert_allclose(result[name]["scaling"]["mean"], np.mean(x, axis=0))
        assert result[name]["scaling"]["fit_observations"] == 8
    changed = copy.deepcopy(rows)
    for row in changed[8:]:
        row["features"] = {k: str(float(v) * 100) for k, v in row["features"].items()}
    other = compare_nine(changed)
    for name in ("logistic", "matched_three"):
        for key in ("scaling", "coefficients", "intercept"):
            assert result[name][key] == other[name][key]
    assert compare_nine(rows, cutoff=0.002)["predictions"] == result["predictions"]
    rows[0]["eligible"] = False
    assert compare_nine(rows)["training_observations"] == 7


def test_three_way_cutoffs_and_tampered_comparator(tmp_path: Path) -> None:
    rows = observations()
    evaluation = compare_nine(rows)
    artifact = tmp_path / "observations.jsonl.gz"
    with gzip.open(artifact, "wt") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    report = dict(
        research_version=NINE_RESEARCH_VERSION,
        data_kind="synthetic",
        evaluation=evaluation,
        limitations=[],
        summaries={
            "test": {
                "eligible": dict(
                    observations=4, positive_observations=1, negative_observations=3
                )
            }
        },
        observation_artifact=dict(
            file=artifact.name,
            rows=len(rows),
            sha256=sha256(artifact.read_bytes()).hexdigest(),
        ),
    )
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    index = evaluate_cutoffs(
        source, list(ILLUSTRATIVE_CUTOFFS), tmp_path / "cutoffs", "nine"
    )
    for entry in index["reports"]:
        saved = json.loads((tmp_path / "cutoffs" / entry["file"]).read_text())
        assert saved["evaluation"]["predictions"] == evaluation["predictions"]
        for model in ("baseline", "matched_three", "logistic"):
            metric = saved["evaluation"][model]["metrics"]
            assert metric["threshold"] == entry["threshold"]
            for key in ("average_precision", "brier_score"):
                assert metric[key] == evaluation[model]["metrics"][key]
    evaluation["predictions"][0]["matched_three_probability"] = 0.99
    source.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="matched_three"):
        evaluate_cutoffs(source, [0.05], tmp_path / "bad", "nine")


@pytest.mark.postgres
def test_database_nine_eligibility_keeps_optional_windows(
    database: Any,
    tmp_path: Path,
) -> None:

    import pandas as pd
    from sqlalchemy.orm import Session

    from indexlens.db.models import PriceSource
    from indexlens.features.context_review import BenchmarkReview
    from indexlens.features.review import Review
    from indexlens.prices.loading import load_price_csv
    from indexlens.research.workflow import run_research
    from tests.test_price_database import metadata
    from tests.test_research_database import setup

    manifest, review, ingestion = setup(database, tmp_path)
    early = tmp_path / "early.csv"
    lines = ["provider_symbol,observation_date,open,high,low,close,volume"]
    for symbol in ("A", "B"):
        for i, stamp in enumerate(pd.bdate_range("2025-07-01", "2025-07-31")):
            value = 95 + i % 7
            lines.append(f"{symbol},{stamp.date()},{value},{value},{value},{value},100")
    early.write_text("\n".join(lines) + "\n")
    loaded = load_price_csv(
        database, early, metadata(dataset_id="synthetic-removal", provider="fixture")
    )
    with Session(database) as session:
        source = session.get(PriceSource, loaded.source_id)
        assert source is not None
        pin = dict(
            source_sha256=source.source_sha256, metadata_sha256=source.metadata_sha256
        )
    for mapping in review["universe"]:
        mapping["start"] = "2025-07-01"
        mapping["sources"].append(pin)
    review["basis_review"]["start"] = "2025-07-01"
    review["financial_context"] = dict(
        currency="USD",
        return_convention="price_return",
        dollar_volume_review=review["basis_review"],
    )
    manifest["price_request_envelope"]["start_inclusive"] = "2025-07-01"
    market = tmp_path / "market.csv"
    all_lines = (tmp_path / "prices.csv").read_text().splitlines()[1:] + lines[1:]
    market.write_text(
        lines[0]
        + "\n"
        + "\n".join(
            line.replace("B,", "^GSPC,", 1)
            for line in all_lines
            if line.startswith("B,")
        )
        + "\n"
    )
    loaded = load_price_csv(
        database, market, metadata(dataset_id="market", provider="fixture")
    )
    with Session(database) as session:
        source = session.get(PriceSource, loaded.source_id)
        assert source is not None
        pin = dict(
            source_sha256=source.source_sha256, metadata_sha256=source.metadata_sha256
        )
    benchmark = {
        k: review[k]
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
        dataset_id="market",
        currency="USD",
        return_convention="price_return",
        benchmark=review["basis_review"]
        | dict(provider_symbol="^GSPC", instrument_type="INDEX", sources=[pin]),
    )
    with Session(database) as session:
        rows, report = run_research(
            session,
            manifest,
            Review.from_dict(review),
            ingestion,
            42,
            benchmark=BenchmarkReview.from_dict(benchmark),
        )
    early_row = next(
        r for r in rows if r["symbol"] == "A" and r["reference_date"] == "2025-09-30"
    )
    assert early_row["eligible"] and early_row["features"]["return_120"] is None
    assert early_row["feature_unavailable"]["return_120"] == ["warm_up"]
    assert all(early_row["features"][f] is not None for f in NINE_FEATURES)
    assert report["evaluation"]["status"] == "evaluated"
    assert report["evaluation"]["training_observations"] == 4
    assert report["evaluation"]["matched_three"]["scaling"]["fit_observations"] == 4
    assert all(r["feature_observation_date"] <= r["reference_date"] for r in rows)
    assert any(
        r["reference_date"] == "2026-05-31"
        and r["feature_observation_date"] == "2026-05-29"
        for r in rows
    )
    benchmark["benchmark"]["sources"][0]["source_sha256"] = "0" * 64
    with Session(database) as session:
        blocked, unavailable = run_research(
            session,
            manifest,
            Review.from_dict(review),
            ingestion,
            42,
            benchmark=BenchmarkReview.from_dict(benchmark),
        )
    assert not any(r["eligible"] for r in blocked)
    assert unavailable["evaluation"]["status"] == "unavailable"
