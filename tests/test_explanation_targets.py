"""Tests for target-specific guidance, company priorities and nested label displays."""

import asyncio
import copy
import json
from typing import Any

import httpx
import pytest

from indexlens.api import openai_provider
from indexlens.api.explanation import ExplainRequest, scalar_references, validate_draft
from indexlens.api.presentation import display_value
from tests.test_explanations import MockProvider, draft_for, provider_response
from tests.test_presentation import comparison_evidence


@pytest.mark.parametrize("attached", [False, True])
def test_company_guidance_and_catalog_prioritize_company(
    monkeypatch: pytest.MonkeyPatch,
    attached: bool,
) -> None:
    research = comparison_evidence()
    bundle: dict[str, Any] = dict(
        metadata=dict(
            data_kind="synthetic", basis_review=dict(description="Synthetic prices")
        ),
        interpretation=dict(boundary="Synthetic context only", data_label="synthetic"),
        result=dict(
            symbol="EXAMPLE_A",
            observation_date="2026-07-31",
            indicators={
                "return_20": {"value": "-0.1", "unit": "fraction"},
                "return_60": {
                    "value": None,
                    "unit": "fraction",
                    "unavailable": ["warm_up"],
                },
                "drawdown_120": {"value": "-0.2", "unit": "fraction"},
            },
        ),
    )
    if attached:
        bundle["saved_research"] = research
        bundle["saved_prediction"] = dict(
            logistic_probability=0.2,
            threshold=0.05,
            context_used_for_fitting=True,
            fitted_features=["return_20"],
            supplementary_indicators=["drawdown_120"],
            timing="Aggregate evaluation is retrospective.",
        )
    before = copy.deepcopy(bundle)
    mock = MockProvider()

    def respond(body: dict[str, Any]) -> httpx.Response:
        payload = json.loads(body["input"][0]["content"])
        return httpx.Response(
            200,
            json=provider_response(
                draft_for(bundle, payload["preferred_context_references"])
            ),
        )

    mock.handler = respond
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", mock.client)
    monkeypatch.setenv("OPENAI_API_KEY", "mock-never-live")
    target = dict(
        kind="financial_context",
        security_id="v1-s0",
        review_id="sample",
        reference_date="2026-07-31",
    )
    if attached:
        target["run_id"] = "sample"
    result = asyncio.run(
        openai_provider.explain(
            ExplainRequest.model_validate(dict(target=target)), bundle
        )
    )
    sent = mock.requests[0]
    prompt = " ".join(sent["instructions"].split())
    assert "Lead with the company and observation date" in prompt
    assert "Company evidence has priority" in prompt
    assert "At most one aggregate performance metric" in prompt
    assert "coherent assessment, not a metric inventory" in prompt
    assert (
        "correct retrospective removal alert does not establish why the committee"
        in prompt
    )
    assert "Observed conditions are not feature-attribution results" in prompt
    assert "A realised outcome omitted from evidence must not be inferred" in prompt
    assert "Increased dollar volume does not identify selling pressure" in prompt
    assert "compare the constant baseline, matched original" not in prompt
    assert "then compare average precision and Brier score for each model" not in prompt
    payload = json.loads(sent["input"][0]["content"])
    preferred = payload["preferred_context_references"]
    assert preferred[:2] == ["/result/symbol", "/result/observation_date"]
    assert all(not p.startswith("/saved_research/") for p in preferred)
    assert "/result/indicators/return_60/value" in preferred  # Nulls stay visible.
    assert ("/saved_prediction/threshold" in preferred) == attached
    assert payload["required_comparison_references"] == []
    catalog = payload["claim_catalog"]
    assert [c["reference"] for c in catalog[: len(preferred)]] == preferred
    assert {c["reference"] for c in catalog} == set(scalar_references(bundle))
    assert payload["evidence"] == result["evidence"] == bundle == before
    if attached:
        labels = {c["reference"]: c for c in catalog}
        assert (
            labels["/saved_research/result/model_labels/logistic"]["display"]
            == "nine-feature model"
        )


@pytest.mark.parametrize("kind", ["event", "ranking"])
def test_other_targets_do_not_receive_research_or_company_structure(kind: str) -> None:
    prompt = openai_provider.instructions_for(kind)
    assert "untrusted" in prompt
    assert "Lead with the company" not in prompt
    assert "compare average precision and Brier score for each model" not in prompt


@pytest.mark.parametrize(
    "model,value,display",
    [
        ("baseline", "Constant-probability baseline", "constant baseline"),
        ("matched_three", "Three-feature logistic regression", "three-feature model"),
        ("logistic", "Nine-feature logistic regression", "nine-feature model"),
    ],
)
def test_nested_labels_preserve_raw_claims(
    model: str, value: str, display: str
) -> None:
    bundle = dict(
        metadata=dict(data_kind="synthetic"),
        interpretation=dict(boundary="Limited", data_label="synthetic"),
        saved_research=dict(result=dict(model_labels={model: value})),
    )
    path = f"/saved_research/result/model_labels/{model}"
    draft = draft_for(bundle, [path])
    draft.explanation = "The {{c0}} supplies the saved comparison."
    result = validate_draft(draft, bundle)
    assert result["explanation"] == f"The {display} [1] supplies the saved comparison."
    assert result["claims"][0]["value_json"] == json.dumps(value)
    assert result["raw_draft"] == draft.model_dump()
    assert result["citations"][0]["reference"] == path
    assert display_value(path, "Unexpected text", {}) == '"Unexpected text"'
    assert display_value(f"/other/model_labels/{model}", value, {}) == json.dumps(value)
