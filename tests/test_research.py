"""Tests for monthly labels, uncertainty, chronological splits and fixed models."""

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy.exc import SQLAlchemyError

from indexlens.data_audit import audit_membership
from indexlens.membership import build_source_events, membership_at, prepare_history
from indexlens.research.__main__ import main as research_main
from indexlens.research.labels import Schedule, label_snapshot
from indexlens.research.model import compare, metrics
from indexlens.research.workflow import feature_dates


def labels(
    records: list[list[str | None]],
    day: date = date(2026, 5, 31),
    coverage_end: date = date(2026, 9, 30),
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(
        records,
        columns=[
            "symbol",
            "cik",
            "name",
            "sector",
            "date_added",
            "date_removed",
            "created_at",
        ],
        dtype="string",
    )
    history = prepare_history(
        audit_membership(frame),
        source="synthetic-research",
        dataset_sha256="a" * 64,
        coverage_start=date(2025, 1, 1),
        coverage_end=coverage_end,
    )
    return label_snapshot(
        membership_at(history, day), build_source_events(history), "test"
    )


@pytest.mark.parametrize(
    "removed,expected",
    [
        ("2026-05-31", None),
        ("2026-06-01", 1),
        ("2026-06-30", 1),
        ("2026-07-01", 0),
        (None, 0),
    ],
)
def test_label_boundaries(removed: str | None, expected: int | None) -> None:
    result = labels(
        [["A", "0001", "Alpha", "Tech", "2025-01-01", removed, "2025-01-01"]]
    )
    if expected is None:
        assert result == []
    else:
        assert result[0]["label_after_source_screen"] == expected


def test_active_record_position_not_just_identity() -> None:
    result = labels(
        [
            ["A", "0001", "Alpha", "Tech", "2025-01-01", "2026-07-01", "2025-01-01"],
            ["A", "0001", "Alpha", "Tech", "2026-06-01", "2026-06-15", "2026-06-01"],
        ]
    )[0]
    assert result["source_record_positions"] == [0]
    assert result["label_after_source_screen"] == 0
    assert result["removal_event_positions"] == []


@pytest.mark.parametrize("removed", [None, "2026-06-05"])
def test_incomplete_outcomes_never_receive_labels(removed: str | None) -> None:
    row = labels(
        [["A", "0001", "Alpha", "Tech", "2025-01-01", removed, "2025-01-01"]],
        coverage_end=date(2026, 6, 10),
    )[0]
    assert row["label_after_source_screen"] is None
    assert "incomplete_outcome_coverage" in row["source_exclusions"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        (4, "2025-01-01*", "approximate_addition_date"),
        (5, "2026-08-01*", "approximate_removal_date"),
    ],
)
def test_approximate_whole_interval_is_unavailable(
    field: int, value: str, reason: str
) -> None:
    record: list[str | None] = [
        "A",
        "0001",
        "Alpha",
        "Tech",
        "2025-01-01",
        None,
        "2025-01-01",
    ]
    record[field] = value
    row = labels([record])[0]
    assert row["label_after_source_screen"] is None
    assert reason in row["source_exclusions"]


@pytest.mark.parametrize(
    "second_cik,reason",
    [("0001", "duplicate_active_identity"), ("0002", "multiple_active_ciks")],
)
def test_ambiguous_membership(second_cik: str, reason: str) -> None:
    rows = labels(
        [
            ["A", "0001", "Alpha", "Tech", "2025-01-01", None, "2025-01-01"],
            ["A", second_cik, "Alpha", "Tech", "2025-02-01", None, "2025-02-01"],
        ]
    )
    assert all(r["label_after_source_screen"] is None for r in rows)
    assert all(reason in r["source_exclusions"] for r in rows)


def test_creation_guard_prevents_future_created_member() -> None:
    assert (
        labels([["A", "0001", "Alpha", "Tech", "2025-01-01", None, "2026-06-01"]]) == []
    )


def test_source_date_conflict_and_approximate_event_screen() -> None:
    frame = pd.DataFrame(
        [["A", "0001", "Alpha", "Tech", "2025-01-01", "2026-06-15", "2025-01-01"]],
        columns=[
            "symbol",
            "cik",
            "name",
            "sector",
            "date_added",
            "date_removed",
            "created_at",
        ],
        dtype="string",
    )
    history = prepare_history(
        audit_membership(frame),
        source="synthetic",
        dataset_sha256="a" * 64,
        coverage_start=date(2025, 1, 1),
        coverage_end=date(2026, 9, 30),
    )
    events = build_source_events(history)
    # Defensive screen for contradictory event evidence supplied by a caller.
    events.data.loc[events.data.event_type.eq("REMOVE"), "created_after_event"] = True
    row = label_snapshot(membership_at(history, date(2026, 5, 31)), events, "test")[0]
    assert row["label_after_source_screen"] is None
    assert "created_after_removal" in row["source_exclusions"]
    events.dataset_sha256 = "b" * 64
    with pytest.raises(ValueError, match="share source"):
        label_snapshot(membership_at(history, date(2026, 5, 31)), events, "test")


def schedule() -> Schedule:
    return Schedule(
        date(2025, 1, 31),
        date(2025, 12, 31),
        date(2026, 1, 31),
        date(2026, 7, 31),
        date(2025, 1, 1),
        date(2026, 8, 1),
    )


def test_split_outcome_boundaries_and_nontrading_reference() -> None:
    refs = schedule().references()
    assert len([r for r in refs if r[0] == "train" and not r[2]]) == 11
    assert ("train", date(2025, 12, 31), ["outcome_crosses_training_cutoff"]) in refs
    assert ("test", date(2026, 7, 31), []) in refs
    days = feature_dates(schedule())
    assert days[date(2026, 1, 31)] == date(2026, 1, 30)
    assert days[date(2026, 5, 31)] == date(2026, 5, 29)
    assert days[date(2025, 8, 31)] == date(2025, 8, 29)


def model_rows() -> list[dict[str, Any]]:
    return [
        dict(
            symbol=f"S{i}",
            cik=str(i),
            reference_date="2025-06-30" if i < 4 else "2026-06-30",
            split="train" if i < 4 else "test",
            eligible=True,
            label_after_source_screen=y,
            features=dict(
                return_20=str(v),
                volatility_20=str(v / 10),
                average_volume_20=str(v * 100),
            ),
        )
        for i, (y, v) in enumerate(
            [(0, 1), (0, 2), (1, 3), (0, 4), (0, 1000), (1, -1000)]
        )
    ]


def test_training_only_scaling_baseline_and_no_test_tuning() -> None:
    rows = model_rows()
    result = compare(rows, 42)
    assert result["status"] == "evaluated"
    assert result["baseline"]["probability"] == 0.25
    assert result["logistic"]["scaling"]["mean"] == pytest.approx([2.5, 0.25, 250])
    assert result["logistic"]["scaling"]["fit_observations"] == 4
    changed = deepcopy(rows)
    for r in changed[4:]:
        r["label_after_source_screen"] = 1 - r["label_after_source_screen"]
        r["features"]["return_20"] = "42"
    second = compare(changed, 42)
    for key in ("scaling", "coefficients", "intercept"):
        assert result["logistic"][key] == second["logistic"][key]
    assert result["parameters"] == second["parameters"]
    assert result == compare(rows, 42)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("empty_train", "no_eligible_training_observations"),
        ("single_train", "training_requires_both_classes"),
        ("empty_test", "no_eligible_test_observations"),
    ],
)
def test_insufficient_classes(case: str, reason: str) -> None:
    rows = model_rows()
    for r in rows:
        if case == "empty_train" and r["split"] == "train":
            r["eligible"] = False
        if case == "empty_test" and r["split"] == "test":
            r["eligible"] = False
        if case == "single_train" and r["split"] == "train":
            r["label_after_source_screen"] = 0
    result = compare(rows, 42)
    assert reason in result["unmet_requirements"]
    assert result["logistic"] is None


def test_metrics_undefined_and_fixed_threshold() -> None:
    values = metrics([0, 0, 1], [0.01, 0.01, 0.01])
    assert values["precision"] is None and values["recall"] == 0 and values["f1"] == 0
    assert values["average_precision"] == pytest.approx(1 / 3)
    assert values["brier_score"] == pytest.approx(0.9803 / 3)
    assert values["confusion_matrix"] == [[2, 0], [1, 0]]
    boundary = metrics([0, 1, 0], [0.049, 0.05, 0.051])
    assert boundary["threshold"] == 0.05
    assert boundary["confusion_matrix"] == [[1, 1], [0, 1]]
    assert boundary["precision"] == 0.5 and boundary["recall"] == 1
    assert boundary["f1"] == pytest.approx(2 / 3)
    assert metrics([0], [0.1])["average_precision"] is None
    assert metrics([], [])["brier_score"] is None
    rows = model_rows()
    for r in rows[4:]:
        r["label_after_source_screen"] = 0
    result = compare(rows, 42)
    assert result["status"] == "evaluation_single_class"
    assert result["logistic"]["metrics"]["average_precision"] is None


def test_repeated_monthly_positive_is_one_distinct_removal() -> None:
    from indexlens.research.workflow import counts

    records: list[list[str | None]] = [
        ["A", "0001", "Alpha", "Tech", "2025-01-01", "2026-03-01", "2025-01-01"]
    ]
    rows = labels(records, date(2026, 1, 31)) + labels(records, date(2026, 2, 28))
    summary = counts(rows, "label_after_source_screen")
    assert summary["positive_observations"] == 2
    assert summary["distinct_removal_events"] == 1


@pytest.mark.parametrize(
    "error,explanation",
    [
        (
            ValueError("Review evidence hash mismatch."),
            "Review evidence hash mismatch.",
        ),
        (FileNotFoundError("missing-review.json"), "missing-review.json"),
        (KeyError("manifest"), "missing required field 'manifest'"),
        (TypeError("invalid seed type"), "invalid seed type"),
        (
            SQLAlchemyError("sensitive-database-detail"),
            "database operation failed (SQLAlchemyError)",
        ),
    ],
)
def test_cli_reports_failure_causes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    explanation: str,
) -> None:
    def fail(
        _config: Path, _output: Path, cutoff: float | None = None
    ) -> dict[str, Any]:
        raise error

    monkeypatch.setattr("indexlens.research.__main__.execute", fail)
    assert research_main(["--config", "config.json", "--output", "data/run"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert explanation in captured.err
    assert "sensitive-database-detail" not in captured.err
