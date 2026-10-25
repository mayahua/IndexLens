"""Readable displays for independently checked evidence scalars."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

PRESENTATION_VERSION = "verified-display-v5"
MODEL_LABELS = {
    "baseline": ("Constant-probability baseline", "constant baseline"),
    "matched_three": ("Three-feature logistic regression", "three-feature model"),
    "logistic": ("Nine-feature logistic regression", "nine-feature model"),
}
INDICATOR_LABELS = {
    "return_20": "short-term returns (20 sessions)",
    "return_60": "medium-term returns (60 sessions)",
    "return_120": "longer-term returns (120 sessions)",
    "volatility_20": "daily return volatility (20 sessions)",
    "average_volume_20": "average trading volume (20 sessions)",
    "average_dollar_volume_20": "average dollar trading volume (20 sessions)",
    "average_dollar_volume_60": "average dollar trading volume (60 sessions)",
    "excess_return_20": "market-relative returns (20 sessions)",
    "excess_return_60": "market-relative returns (60 sessions)",
    "excess_return_120": "market-relative returns (120 sessions)",
    "benchmark_return_20": "benchmark returns (20 sessions)",
    "benchmark_return_60": "benchmark returns (60 sessions)",
    "benchmark_return_120": "benchmark returns (120 sessions)",
    "benchmark_volatility_20": "benchmark daily return volatility (20 sessions)",
    "relative_volatility_20": "market-relative volatility (20 sessions)",
    "dollar_volume_20_to_60_ratio": "ratio of recent to longer-term dollar volume",
    "drawdown_120": "drawdown from the trailing closing high (120 sessions)",
}
SMALL_COUNTS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
ALERT_COUNTS = {
    "predicted_removals",
    "true_positives",
    "false_alarms",
    "false_negatives",
}


def preferred_context_references(
    evidence: dict[str, Any], target_kind: str = "run"
) -> list[str]:
    """Prioritize the selected target's context within the claim allowance."""
    if target_kind == "financial_context":
        return [
            "/result/symbol",
            "/result/observation_date",
            *[
                f"/result/indicators/{name}/value"
                for name in (
                    "return_20",
                    "return_60",
                    "excess_return_20",
                    "excess_return_60",
                    "volatility_20",
                    "relative_volatility_20",
                    "drawdown_120",
                    "average_dollar_volume_20",
                    "average_dollar_volume_60",
                    "dollar_volume_20_to_60_ratio",
                )
            ],
            *[
                f"/saved_prediction/supplementary_indicators/{i}"
                for i, name in enumerate(
                    evidence.get("saved_prediction", {}).get(
                        "supplementary_indicators", []
                    )
                )
                if name in {"return_120", "drawdown_120"}
            ],
            "/saved_prediction/context_used_for_fitting",
            "/saved_prediction/logistic_probability",
            "/saved_prediction/threshold",
            "/saved_prediction/timing",
            "/metadata/basis_review/description",
            "/interpretation/boundary",
        ]
    if target_kind != "run":
        return []
    if not required_comparison_references(evidence):
        return []
    return [
        "/result/split/label_horizon_days",
        "/result/split/train_reference_start",
        "/result/split/train_outcome_cutoff",
        "/result/split/test_reference_start",
        "/result/split/test_reference_end",
        "/result/summaries/train/eligible/observations",
        "/result/summaries/train/eligible/positive_observations",
        "/result/summaries/test/eligible/observations",
        "/result/summaries/test/eligible/positive_observations",
        "/result/evaluation/baseline/probability",
    ]


PERCENT_FIELDS = {
    "threshold",
    "selected_threshold",
    "source_threshold",
    "precision",
    "recall",
    "f1",
    "probability",
    "logistic_probability",
    "baseline_probability",
    "matched_three_probability",
}


def display_value(reference: str, value: Any, references: dict[str, Any]) -> str:
    """Format a validated scalar; raw values and pointers remain in citations."""
    field = reference.rsplit("/", 1)[-1]
    if isinstance(value, str):
        # Keep the approved cutoff-derivation kind in citations, not reader prose.
        if (
            reference == "/result/evaluation/derivation/kind"
            and value == "saved_probability_cutoff_evaluation"
        ):
            return ""
        if reference == "/saved_prediction/timing" and value == (
            "Saved evaluation metrics are retrospective, "
            "not facts known at the reference date."
        ):
            return ""
        if (
            reference == "/result/symbol"
            and len(value) <= 15
            and re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?", value)
        ):
            return value
        model = MODEL_LABELS.get(field)
        if (
            reference
            in {
                f"/result/model_labels/{field}",
                f"/saved_research/result/model_labels/{field}",
            }
            and model
            and value == model[0]
        ):
            return model[1]
        if (
            re.fullmatch(
                r"/(?:result/evaluation|saved_prediction)/(?:fitted_features|features|comparator_features)/\d+"
                r"|/saved_prediction/supplementary_indicators/\d+",
                reference,
            )
            and value in INDICATOR_LABELS
        ):
            return INDICATOR_LABELS[value]
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            day = date.fromisoformat(value)
            return f"{day.day} {day.strftime('%B %Y')}"
        except ValueError:
            pass
    unit = references.get(reference.rsplit("/", 1)[0] + "/unit")
    numeric = isinstance(value, (float, int)) or (
        isinstance(value, str)
        and unit
        in {
            "fraction",
            "fraction_difference",
            "daily_sample_std",
            "USD_per_session",
            "ratio",
        }
    )
    if numeric:
        try:
            with localcontext() as ctx:
                ctx.prec = 40
                number = Decimal(str(value))
                if not number.is_finite():
                    return "unavailable"
                if field in PERCENT_FIELDS or unit in {"fraction", "daily_sample_std"}:
                    return f"{number * 100:,.2f}%"
                if unit == "fraction_difference":
                    return f"{number * 100:,.2f} percentage points"
                if field == "average_precision":
                    return f"{number:.5f}"
                if field == "brier_score":
                    return f"{number:.8f}"
                if unit == "USD_per_session":
                    return f"${number:,.2f} per session"
                if unit == "ratio":
                    return f"{number:.3f}×"
                if isinstance(value, int):
                    if field in ALERT_COUNTS and 0 <= value < len(SMALL_COUNTS):
                        return SMALL_COUNTS[value]
                    return f"{value:,}"
                return f"{number:.6g}"
        except (InvalidOperation, ValueError):
            pass
    # Long source/limitation strings are available through the citation, rather
    # than being injected as paragraphs into the analyst's sentence.
    if isinstance(value, str) and (
        len(value) > 120
        or "/limitations/" in reference
        or field in {"boundary", "review_limitations", "limitations"}
    ):
        return ""
    return (
        json.dumps(value, ensure_ascii=True) if isinstance(value, str) else str(value)
    )


def required_comparison_references(evidence: dict[str, Any]) -> set[str]:
    """Require numerical coverage of each comparator for supported run prose."""
    evaluation = evidence.get("result", {}).get("evaluation", {})
    if (
        evidence.get("metadata", {}).get("research_version")
        != "monthly-removal-nine-v1"
        or evaluation.get("status") != "evaluated"
    ):
        return set()
    return {
        f"/result/evaluation/{model}/metrics/{metric}"
        for model in ("baseline", "matched_three", "logistic")
        for metric in ("average_precision", "brier_score")
    } | {
        *[
            f"/result/model_labels/{model}"
            for model in ("baseline", "matched_three", "logistic")
        ],
        "/result/evaluation/threshold",
        *[
            f"/result/evaluation/logistic/metrics/{key}"
            for key in (
                "predicted_removals",
                "true_positives",
                "false_alarms",
                "false_negatives",
                "precision",
                "recall",
            )
        ],
    }
