"""Fixed training-frequency and logistic models on the same eligible rows."""

import warnings
from math import isfinite
from numbers import Real
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import average_precision_score  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from indexlens.features.core import FEATURES

MODEL_VERSION = "scaled-logistic-v1"
DEFAULT_CUTOFF = 0.05
NINE_FEATURES = (
    "return_20",
    "volatility_20",
    "return_60",
    "average_dollar_volume_20",
    "average_dollar_volume_60",
    "excess_return_20",
    "excess_return_60",
    "relative_volatility_20",
    "dollar_volume_20_to_60_ratio",
)
NINE_FEATURE_VERSION = "removal-nine-features-v1"
NINE_MODEL_VERSION = "scaled-logistic-nine-v1"
NINE_RESEARCH_VERSION = "monthly-removal-nine-v1"
ILLUSTRATIVE_CUTOFFS = (0.1, 0.05, 0.01, 0.005, 0.002)


def validate_cutoff(value: object) -> float:
    """Return a finite numeric probability in [0, 1]; booleans are invalid."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Cutoff must be a finite number in [0, 1].")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("Cutoff must be a finite number in [0, 1].") from exc
    if not 0 <= number <= 1 or not isfinite(number):
        raise ValueError("Cutoff must be a finite number in [0, 1].")
    return number


def cutoff_limitation(cutoff: float) -> str:
    return (
        f"The selected {cutoff:g} classification cutoff is exploratory; "
        "performance on unseen data remains unverified."
    )


def metrics(
    labels: list[int], probabilities: list[float], cutoff: float = DEFAULT_CUTOFF
) -> dict[str, Any]:
    """Undefined denominators are null, with explicit reasons."""
    cutoff = validate_cutoff(cutoff)
    if len(labels) != len(probabilities) or any(
        type(y) is not int or y not in (0, 1) for y in labels
    ):
        raise ValueError("Invalid evaluation inputs.")
    if any(
        isinstance(p, bool)
        or not isinstance(p, Real)
        or not 0 <= p <= 1
        or not isfinite(p)
        for p in probabilities
    ):
        raise ValueError("Invalid probabilities.")
    y = np.asarray(labels, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    prediction = p >= cutoff
    tp = int(np.sum((y == 1) & prediction))
    fp = int(np.sum((y == 0) & prediction))
    tn = int(np.sum((y == 0) & ~prediction))
    fn = int(np.sum((y == 1) & ~prediction))
    undefined = {}
    values: dict[str, Any] = {}
    for name, numerator, denominator in (
        ("precision", tp, tp + fp),
        ("recall", tp, tp + fn),
        ("f1", 2 * tp, 2 * tp + fp + fn),
    ):
        values[name] = numerator / denominator if denominator else None
        if not denominator:
            undefined[name] = "zero_denominator"
    values["average_precision"] = (
        float(average_precision_score(y, p)) if tp + fn else None
    )
    if not tp + fn:
        undefined["average_precision"] = "no_positive_outcomes"
    values["brier_score"] = float(np.mean((p - y) ** 2)) if len(y) else None
    if not len(y):
        undefined["brier_score"] = "empty_evaluation_set"
    return values | dict(
        observations=len(labels),
        predicted_removals=tp + fp,
        false_alarms=fp,
        true_positives=tp,
        false_negatives=fn,
        threshold=cutoff,
        confusion_matrix=[[tn, fp], [fn, tp]],
        confusion_matrix_order="rows actual [0,1], columns predicted [0,1]",
        undefined=undefined,
        limitations=["Evaluation contains fewer than two classes."]
        if len(set(labels)) < 2
        else [],
    )


def compare(
    rows: list[dict[str, Any]],
    seed: int,
    cutoff: float = DEFAULT_CUTOFF,
    *,
    features: tuple[str, ...] = FEATURES,
) -> dict[str, Any]:
    cutoff = validate_cutoff(cutoff)
    train = [r for r in rows if r["eligible"] and r["split"] == "train"]
    test = [r for r in rows if r["eligible"] and r["split"] == "test"]
    y_train = [int(r["label_after_source_screen"]) for r in train]
    y_test = [int(r["label_after_source_screen"]) for r in test]
    parameters = dict(
        C=1.0,
        solver="lbfgs",
        penalty="l2",
        fit_intercept=True,
        class_weight=None,
        max_iter=1000,
        tol=1e-8,
        random_state=seed,
    )
    result: dict[str, Any] = dict(
        model_version=MODEL_VERSION,
        features=list(features),
        seed=seed,
        threshold=cutoff,
        parameters=parameters,
        status="unavailable",
        training_observations=len(train),
        test_observations=len(test),
        baseline=None,
        logistic=None,
        predictions=[],
        unmet_requirements=[],
    )
    if not train:
        result["unmet_requirements"].append("no_eligible_training_observations")
        return result
    frequency = sum(y_train) / len(y_train)
    result["baseline"] = dict(
        model_version="training-frequency-v1",
        probability=frequency,
        metrics=metrics(y_test, [frequency] * len(test), cutoff),
    )
    if len(set(y_train)) < 2:
        result["unmet_requirements"].append("training_requires_both_classes")
    if not test:
        result["unmet_requirements"].append("no_eligible_test_observations")
    if result["unmet_requirements"]:
        return result
    x_train = np.asarray(
        [[float(r["features"][f]) for f in features] for r in train], dtype=float
    )
    x_test = np.asarray(
        [[float(r["features"][f]) for f in features] for r in test], dtype=float
    )
    if not np.isfinite(x_train).all() or not np.isfinite(x_test).all():
        raise ValueError("Nonfinite model features.")
    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(x_train)
    classifier = LogisticRegression(**parameters)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ConvergenceWarning)
        classifier.fit(scaled_train, y_train)
    if any(issubclass(w.category, ConvergenceWarning) for w in captured):
        result["unmet_requirements"].append("logistic_did_not_converge")
        return result
    probabilities = classifier.predict_proba(scaler.transform(x_test))[:, 1].tolist()
    result["logistic"] = dict(
        metrics=metrics(y_test, probabilities, cutoff),
        scaling=dict(
            mean=scaler.mean_.tolist(),
            scale=scaler.scale_.tolist(),
            variance=scaler.var_.tolist(),
            fit_observations=len(train),
        ),
        coefficients=classifier.coef_[0].tolist(),
        intercept=float(classifier.intercept_[0]),
        classes=classifier.classes_.tolist(),
        iterations=classifier.n_iter_.tolist(),
    )
    result["predictions"] = [
        dict(
            symbol=r["symbol"],
            cik=r["cik"],
            reference_date=r["reference_date"],
            label=y,
            baseline_probability=frequency,
            logistic_probability=p,
        )
        for r, y, p in zip(test, y_test, probabilities, strict=True)
    ]
    result["status"] = (
        "evaluated" if len(set(y_test)) == 2 else "evaluation_single_class"
    )
    return result


def compare_nine(
    rows: list[dict[str, Any]], seed: int = 42, cutoff: float = DEFAULT_CUTOFF
) -> dict[str, Any]:
    """Fit the frozen specification and original comparator on identical rows."""
    result = compare(rows, seed, cutoff, features=NINE_FEATURES)
    original = compare(rows, seed, cutoff)
    result.update(
        model_version=NINE_MODEL_VERSION,
        fitted_features=list(NINE_FEATURES),
        matched_three=original["logistic"],
        comparator_features=list(FEATURES),
        comparator_model_version=MODEL_VERSION,
    )
    if original["unmet_requirements"] or result["unmet_requirements"]:
        result["status"] = "unavailable"
        result["unmet_requirements"] = sorted(
            set(result["unmet_requirements"] + original["unmet_requirements"])
        )
        result["predictions"] = []
        return result
    test = [r for r in rows if r["eligible"] and r["split"] == "test"]
    for prediction, comparator, row in zip(
        result["predictions"], original["predictions"], test, strict=True
    ):
        prediction.update(
            matched_three_probability=comparator["logistic_probability"],
            feature_observation_date=row["feature_observation_date"],
            fitted_values={f: row["features"][f] for f in NINE_FEATURES},
            context_input_sha256=row["context_input_sha256"],
            source_record_positions=row["source_record_positions"],
        )
    return result
