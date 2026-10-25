"""Tests for exact nine-feature prediction joins and optional financial context."""

import copy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from indexlens.api.explanation import FinancialTarget, financial_evidence
from indexlens.api.service import ApiError
from indexlens.features.context import calculate_context
from indexlens.research.model import NINE_FEATURES, NINE_RESEARCH_VERSION
from tests.test_financial_context import DAY, history


def setup() -> tuple[Any, FinancialTarget, dict[str, Any], dict[str, Any]]:
    rows = history()[-61:]
    rows[-1] = replace(rows[-1], close=Decimal(110))
    result = calculate_context(
        rows, rows, DAY, dollar_volume_approved=True, comparison_approved=True
    )
    result.update(symbol="A", cik="0001", research_input_sha256=result["input_sha256"])
    bundle = dict(
        result=result,
        metadata=dict(
            data_kind="synthetic",
            membership_sha256="a" * 64,
            review_sha256="b" * 64,
            source_record_positions=[1],
            benchmark=dict(review_sha256="c" * 64),
        ),
    )
    prediction = dict(
        symbol="A",
        cik="0001",
        reference_date=str(DAY),
        feature_observation_date=str(DAY),
        fitted_values={f: result["indicators"][f]["value"] for f in NINE_FEATURES},
        context_input_sha256=result["research_input_sha256"],
        source_record_positions=[1],
        logistic_probability=0.05,
        baseline_probability=0.01,
        label=1,
    )
    report = dict(
        research_version=NINE_RESEARCH_VERSION,
        data_kind="synthetic",
        membership_sha256="a" * 64,
        review_sha256="b" * 64,
        benchmark_review_sha256="c" * 64,
        evaluation=dict(
            threshold=0.05,
            fitted_features=list(NINE_FEATURES),
            predictions=[prediction],
        ),
    )
    service: Any = SimpleNamespace(
        financial_context=lambda *a: copy.deepcopy(bundle),
        research=SimpleNamespace(
            run=lambda _: report, run_result=lambda _: dict(result={})
        ),
    )
    target = FinancialTarget.model_validate(
        dict(
            kind="financial_context",
            security_id="v1-s0",
            review_id="sample",
            reference_date=str(DAY),
            benchmark_id="market",
            run_id="nine",
        )
    )
    return service, target, bundle, report


def test_nine_join_keeps_missing_supplementary_values() -> None:
    service, target, _, _ = setup()
    evidence = financial_evidence(service, target)
    prediction = evidence["saved_prediction"]
    assert prediction["predicted_removal"]
    assert prediction["context_used_for_fitting"]
    assert prediction["fitted_features"] == list(NINE_FEATURES)
    assert "label" not in prediction
    assert "drawdown_120" in prediction["supplementary_indicators"]
    assert evidence["result"]["indicators"]["return_120"]["value"] is None


@pytest.mark.parametrize(
    "damage",
    [
        "identity",
        "kind",
        "membership",
        "date",
        "observation_date",
        "value",
        "input_hash",
        "review",
        "benchmark",
        "features",
        "positions",
        "duplicate",
    ],
)
def test_mismatched_nine_join_rejected(damage: str) -> None:
    service, target, bundle, report = setup()
    p = report["evaluation"]["predictions"][0]
    if damage == "identity":
        p["cik"] = "0002"
    elif damage == "kind":
        report["data_kind"] = "real"
    elif damage == "membership":
        report["membership_sha256"] = "0" * 64
    elif damage == "date":
        p["reference_date"] = "2026-01-01"
    elif damage == "observation_date":
        p["feature_observation_date"] = "2026-01-01"
    elif damage == "value":
        p["fitted_values"]["return_20"] = "0.9"
    elif damage == "input_hash":
        p["context_input_sha256"] = "0" * 64
    elif damage == "review":
        report["review_sha256"] = "0" * 64
    elif damage == "benchmark":
        bundle["metadata"]["benchmark"] = None
    elif damage == "features":
        report["evaluation"]["fitted_features"].reverse()
    elif damage == "positions":
        p["source_record_positions"] = [99]
    elif damage == "duplicate":
        report["evaluation"]["predictions"].append(copy.deepcopy(p))
    with pytest.raises(ApiError) as error:
        financial_evidence(service, target)
    assert error.value.status == 422
