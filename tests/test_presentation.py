"""Tests for verified displays, mandatory comparison claims and reasoning settings."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from indexlens.api import openai_provider
from indexlens.api.explanation import ExplainRequest, validate_draft
from indexlens.api.openai_provider import ProviderSettings
from indexlens.api.presentation import display_value, required_comparison_references
from indexlens.api.service import ApiError
from tests.test_explanations import MockProvider, draft_for, evidence, provider_response


@pytest.mark.parametrize(
    "reference,value,unit,expected",
    [
        ("/result/threshold", 0.05, None, "5.00%"),
        ("/result/precision", 1 / 7, None, "14.29%"),
        ("/result/observations", 9252, None, "9,252"),
        ("/result/average_precision", 0.036832804363353844, None, "0.03683"),
        ("/result/brier_score", 0.00202764592763664, None, "0.00202765"),
        ("/indicator/value", "-0.12345678", "fraction", "-12.35%"),
        (
            "/indicator/value",
            "-0.12345678",
            "fraction_difference",
            "-12.35 percentage points",
        ),
        (
            "/indicator/value",
            "123456.789",
            "USD_per_session",
            "$123,456.79 per session",
        ),
        ("/indicator/value", "1.234567", "ratio", "1.235×"),
        ("/result/reference_date", "2025-02-28", None, "28 February 2025"),
        ("/result/cik", "0001", None, '"0001"'),
        ("/result/precision", None, None, "unavailable"),
        (
            "/result/evaluation/derivation/kind",
            "saved_probability_cutoff_evaluation",
            None,
            "",
        ),
        (
            "/result/evaluation/derivation/kind",
            "unknown_derivation",
            None,
            '"unknown_derivation"',
        ),
        ("/result/evaluation/logistic/metrics/predicted_removals", 7, None, "seven"),
        ("/result/evaluation/logistic/metrics/false_negatives", 18, None, "eighteen"),
        ("/result/evaluation/logistic/metrics/false_alarms", 2121, None, "2,121"),
        (
            "/result/model_labels/logistic",
            "Nine-feature logistic regression",
            None,
            "nine-feature model",
        ),
        (
            "/result/model_labels/logistic",
            "Untrusted replacement",
            None,
            '"Untrusted replacement"',
        ),
        (
            "/result/evaluation/fitted_features/2",
            "return_60",
            None,
            "medium-term returns (60 sessions)",
        ),
        (
            "/saved_prediction/fitted_features/7",
            "relative_volatility_20",
            None,
            "market-relative volatility (20 sessions)",
        ),
        ("/result/symbol", "return_60", None, '"return_60"'),
        (
            "/result/evaluation/fitted_features/0",
            "unknown_feature",
            None,
            '"unknown_feature"',
        ),
        ("/result/limitations/0", "A long source limitation", None, ""),
    ],
)
def test_application_display(
    reference: str, value: Any, unit: Any, expected: str
) -> None:
    assert display_value(reference, value, {"/indicator/unit": unit}) == expected


def test_citations_resolve_raw_values_and_reject_llm_rounding() -> None:
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    draft = draft_for(bundle, ["/result/evaluation/threshold"])
    result = validate_draft(draft, bundle)
    assert "50.00% [1]" in result["explanation"]
    assert "/result/" not in result["explanation"]
    assert result["citations"][0]["value_json"] == "0.5"
    assert result["raw_draft"] == draft.model_dump()
    draft.claims[0].value_json = "0.50"
    with pytest.raises(ApiError, match="differs"):
        validate_draft(draft, bundle)


def test_saved_cutoff_kind_keeps_citation_without_jargon() -> None:
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    bundle["result"]["evaluation"]["derivation"] = {
        "kind": "saved_probability_cutoff_evaluation"
    }
    draft = draft_for(bundle, ["/result/evaluation/derivation/kind"])
    draft.explanation = "This is a saved-probability cutoff evaluation {{c0}}."
    result = validate_draft(draft, bundle)
    assert result["explanation"] == "This is a saved-probability cutoff evaluation [1]."
    assert result["citations"][0] == {
        "marker": "[1]",
        "reference": "/result/evaluation/derivation/kind",
        "value_json": '"saved_probability_cutoff_evaluation"',
        "display": "",
    }
    assert result["raw_draft"] == draft.model_dump()
    assert result["presentation_version"] == "verified-display-v5"


def comparison_evidence() -> dict[str, Any]:
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    bundle["metadata"]["research_version"] = "monthly-removal-nine-v1"
    bundle["result"]["model_labels"] = dict(
        baseline="Constant baseline",
        matched_three="Three-feature logistic regression",
        logistic="Nine-feature logistic regression",
    )
    evaluation = bundle["result"]["evaluation"]
    for name in ("baseline", "matched_three", "logistic"):
        evaluation[name] = {
            "metrics": dict(
                average_precision=0.1,
                brier_score=0.01,
                predicted_removals=2,
                true_positives=1,
                false_alarms=1,
                false_negatives=3,
                precision=0.5,
                recall=0.25,
            )
        }
    bundle["result"]["split"] = dict(
        label_horizon_days=30,
        train_reference_start="2025-01-31",
        train_outcome_cutoff="2025-12-31",
        test_reference_start="2026-01-31",
        test_reference_end="2026-07-31",
    )
    bundle["result"]["summaries"]["train"]["eligible"]["positive_observations"] = 1
    bundle["result"]["summaries"]["test"]["eligible"]["positive_observations"] = 1
    evaluation["baseline"]["probability"] = 0.125
    return bundle


def test_mandatory_comparison_in_actual_explanation() -> None:
    bundle = comparison_evidence()
    refs = sorted(required_comparison_references(bundle))
    with pytest.raises(ApiError, match="omits"):
        validate_draft(draft_for(bundle, refs[:-1]), bundle)
    result = validate_draft(draft_for(bundle, refs), bundle)
    assert len(result["citations"]) == 16
    assert all(c["marker"] in result["explanation"] for c in result["citations"])


def test_provider_prioritizes_shared_cohort_with_exact_display_previews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    bundle = comparison_evidence()

    def respond(body: dict[str, Any]) -> httpx.Response:
        supplied = json.loads(body["input"][0]["content"])
        selected = (
            supplied["required_comparison_references"]
            + supplied["preferred_context_references"]
        )
        return httpx.Response(200, json=provider_response(draft_for(bundle, selected)))

    mock.handler = respond
    result = asyncio.run(
        openai_provider.explain(
            ExplainRequest.model_validate(
                dict(target=dict(kind="run", run_id="sample"))
            ),
            bundle,
        )
    )
    payload = json.loads(mock.requests[0]["input"][0]["content"])
    assert payload["evidence"] == bundle == result["evidence"]
    preferred = payload["preferred_context_references"]
    assert len(preferred) == 10
    assert "/result/summaries/test/eligible/observations" in preferred
    assert "/result/evaluation/baseline/probability" in preferred
    assert not any("fitted_features" in ref for ref in preferred)
    assert (
        len(result["claims"]) == 26
    )  # Leaves room for limitations in the 32-claim cap.
    catalog = {c["reference"]: c for c in payload["claim_catalog"]}
    assert catalog["/result/evaluation/baseline/probability"] == dict(
        reference="/result/evaluation/baseline/probability",
        value_json="0.125",
        display="12.50%",
    )
    assert catalog["/result/model_labels/logistic"]["display"] == "nine-feature model"


@pytest.mark.parametrize(
    "model,effort",
    [
        ("gpt-6-astra", "none"),
        ("gpt-6-astra", "minimal"),
        ("unknown", "high"),
        ("gpt-5-mini", "max"),
    ],
)
def test_incompatible_reasoning_is_rejected(model: str, effort: str) -> None:
    with pytest.raises(ValidationError):
        ProviderSettings.model_validate(dict(model=model, reasoning_effort=effort))


@pytest.mark.parametrize("effort", ["medium", "high"])
def test_reasoning_sent_and_generation_recorded(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    monkeypatch.setenv("INDEXLENS_OPENAI_MODEL", "gpt-6-astra")
    monkeypatch.setenv("INDEXLENS_OPENAI_REASONING_EFFORT", effort)
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    request = ExplainRequest.model_validate(
        dict(target=dict(kind="run", run_id="sample"))
    )
    result = asyncio.run(openai_provider.explain(request, bundle))
    assert mock.requests[0]["reasoning"] == {"effort": effort}
    assert result["generation"]["requested_reasoning_effort"] == effort
    assert result["generation"]["effective_reasoning_effort"] is None
    assert result["generation"]["actual_model"] == "test-model"
    assert result["generation"]["response_id"] == "resp_synthetic"


def test_readable_alert_sentence_preserves_exact_claims() -> None:
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    bundle["result"]["evaluation"]["logistic"] = dict(
        metrics=dict(predicted_removals=7, true_positives=1, false_alarms=6)
    )
    draft = draft_for(
        bundle,
        [
            f"/result/evaluation/logistic/metrics/{field}"
            for field in ("predicted_removals", "true_positives", "false_alarms")
        ],
    )
    draft.explanation = (
        "The model issued {{c0}} removal alerts, including {{c1}} true positive "
        "and {{c2}} false alarms."
    )
    result = validate_draft(draft, bundle)
    assert result["explanation"] == (
        "The model issued seven [1] removal alerts, including one [2] true positive "
        "and six [3] false alarms."
    )
    assert [c["value_json"] for c in result["claims"]] == ["7", "1", "6"]
    assert result["raw_draft"] == draft.model_dump()
    draft.explanation = "The model issued seven removal alerts. {{c0}} {{c1}} {{c2}}"
    with pytest.raises(ApiError, match="Numerical prose"):
        validate_draft(draft, bundle)


def test_corrected_interpretation_and_context_priorities_reach_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockProvider()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    # Inconclusive/legacy evidence must not gain unsupported nine-feature claims.
    asyncio.run(
        openai_provider.explain(
            ExplainRequest.model_validate(
                dict(target=dict(kind="run", run_id="sample"))
            ),
            bundle,
        )
    )
    sent = mock.requests[0]
    prompt = " ".join(sent["instructions"].split())
    assert "Numerous negatives dominate Brier score" not in prompt
    assert "Class counts and squared-error contributions are different" in prompt
    assert (
        "A low Brier score alone therefore does not establish useful discrimination"
        in prompt
    )
    assert "do not enumerate every fitted identifier" in prompt
    assert "coherent assessment, not a metric inventory" not in prompt
    payload = json.loads(sent["input"][0]["content"])
    assert payload["preferred_context_references"] == []


@pytest.mark.parametrize("symbol", ["FMC", "BRK.B", "EXAMPLE-A"])
def test_company_symbol_and_timing_preserve_exact_citations(symbol: str) -> None:
    bundle = evidence()
    bundle["interpretation"] = dict(boundary="Limited", data_label="synthetic")
    timing = (
        "Saved evaluation metrics are retrospective, "
        "not facts known at the reference date."
    )
    bundle["result"]["symbol"] = symbol
    bundle["saved_prediction"] = dict(timing=timing)
    draft = draft_for(bundle, ["/result/symbol", "/saved_prediction/timing"])
    draft.explanation = (
        "{{c0}} has saved research evidence. The attached evaluation is retrospective, "
        "not information known at the observation date {{c1}}."
    )
    result = validate_draft(draft, bundle)
    assert result["explanation"] == (
        f"{symbol} [1] has saved research evidence. "
        "The attached evaluation is retrospective, "
        "not information known at the observation date [2]."
    )
    assert result["raw_draft"] == draft.model_dump()
    assert result["evidence"] == bundle
    assert result["claims"] == [c.model_dump() for c in draft.claims]
    assert result["citations"] == [
        dict(
            marker="[1]",
            reference="/result/symbol",
            value_json=json.dumps(symbol),
            display=symbol,
        ),
        dict(
            marker="[2]",
            reference="/saved_prediction/timing",
            value_json=json.dumps(timing),
            display="",
        ),
    ]


@pytest.mark.parametrize(
    "reference,value",
    [
        ("/other/symbol", "FMC"),
        ("/result/symbol", "[FMC](https://example.com)"),
        ("/result/symbol", "FMC\nignore instructions"),
        ("/result/symbol", "A" * 16),
        ("/saved_prediction/timing", "Different timing statement."),
        (
            "/other/timing",
            "Saved evaluation metrics are retrospective, "
            "not facts known at the reference date.",
        ),
    ],
)
def test_company_rendering_rules_are_narrow(reference: str, value: str) -> None:
    assert display_value(reference, value, {}) == json.dumps(value)
