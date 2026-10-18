"""Fixed training-frequency and logistic models on the same eligible rows."""

import warnings
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import average_precision_score  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from indexlens.features.core import FEATURES

MODEL_VERSION = "scaled-logistic-v1"
THRESHOLD = 0.5


def metrics(labels: list[int], probabilities: list[float]) -> dict[str, Any]:
    """Undefined denominators are null, with explicit reasons."""
    if len(labels) != len(probabilities) or any(y not in (0, 1) for y in labels):
        raise ValueError("Invalid evaluation inputs.")
    if any(not np.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("Invalid probabilities.")
    y = np.asarray(labels, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    prediction = p >= THRESHOLD
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
        threshold=THRESHOLD,
        confusion_matrix=[[tn, fp], [fn, tp]],
        confusion_matrix_order="rows actual [0,1], columns predicted [0,1]",
        undefined=undefined,
        limitations=["Evaluation contains fewer than two classes."]
        if len(set(labels)) < 2
        else [],
    )


def compare(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
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
        features=list(FEATURES),
        seed=seed,
        threshold=THRESHOLD,
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
        metrics=metrics(y_test, [frequency] * len(test)),
    )
    if len(set(y_train)) < 2:
        result["unmet_requirements"].append("training_requires_both_classes")
    if not test:
        result["unmet_requirements"].append("no_eligible_test_observations")
    if result["unmet_requirements"]:
        return result
    x_train = np.asarray(
        [[float(r["features"][f]) for f in FEATURES] for r in train], dtype=float
    )
    x_test = np.asarray(
        [[float(r["features"][f]) for f in FEATURES] for r in test], dtype=float
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
        metrics=metrics(y_test, probabilities),
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
