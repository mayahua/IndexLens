"""Tests for explicit Yahoo benchmark instrument selection and saved probabilities."""

import copy
import json
from typing import Any

import pytest

from indexlens.api.explanation import FinancialTarget, financial_evidence
from indexlens.api.service import ApiError
from indexlens.prices.yahoo import YahooValidationError, chart_records
from tests.test_yahoo import END, FIXTURE, START


@pytest.mark.parametrize("symbol,instrument", [("^GSPC", "INDEX"), ("SPY", "ETF")])
def test_benchmark_instruments_require_explicit_selection(
    symbol: str, instrument: str
) -> None:
    raw = json.loads(FIXTURE.read_bytes())
    raw["chart"]["result"][0]["meta"].update(symbol=symbol, instrumentType=instrument)
    payload = json.dumps(raw).encode()
    with pytest.raises(YahooValidationError):
        chart_records(payload, symbol, START, END)
    rows, meta = chart_records(payload, symbol, START, END, instrument_type=instrument)
    assert rows and meta["instrumentType"] == instrument


@pytest.mark.parametrize("cutoff,expected", [(0.5, False), (0.05, True), (0.01, True)])
def test_saved_prediction_uses_selected_cutoff(cutoff: float, expected: bool) -> None:
    from types import SimpleNamespace

    from indexlens.features.context import calculate_context
    from tests.test_financial_context import DAY, history

    bundle = dict(
        result=calculate_context(history(), [], DAY),
        metadata=dict(data_kind="synthetic", membership_sha256="a" * 64),
    )
    bundle["result"].update(symbol="A", cik="0001")
    report = dict(
        data_kind="synthetic",
        membership_sha256="a" * 64,
        evaluation=dict(
            threshold=cutoff,
            predictions=[
                dict(
                    symbol="A",
                    cik="0001",
                    reference_date=str(DAY),
                    logistic_probability=0.05,
                    baseline_probability=0.01,
                )
            ],
        ),
    )
    service: Any = SimpleNamespace(
        financial_context=lambda *args: copy.deepcopy(bundle),
        research=SimpleNamespace(
            run=lambda _: report, run_result=lambda _: copy.deepcopy(report)
        ),
    )
    target = FinancialTarget.model_validate(
        dict(
            kind="financial_context",
            security_id="v1-s0",
            review_id="sample",
            reference_date=str(DAY),
            run_id="saved",
        )
    )
    result = financial_evidence(service, target)
    assert result["saved_prediction"]["threshold"] == cutoff
    assert result["saved_prediction"]["predicted_removal"] == expected
    assert not result["saved_prediction"]["context_used_for_fitting"]
    report["data_kind"] = "real"
    with pytest.raises(ApiError, match="Research source differs"):
        financial_evidence(service, target)
