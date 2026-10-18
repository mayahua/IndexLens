"""Thirty-day source labels on guarded monthly membership snapshots."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from indexlens.membership import MembershipSnapshot, SourceEventTable


@dataclass(frozen=True)
class Schedule:
    train_start: date
    train_cutoff: date
    test_start: date
    test_end: date
    price_start: date
    price_end: date

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "Schedule":
        if manifest.get("label_horizon_days") != 30:
            raise ValueError("Research requires a 30-calendar-day outcome window.")
        envelope = manifest["price_request_envelope"]
        result = cls(
            date.fromisoformat(manifest["train_reference_start"]),
            date.fromisoformat(manifest["train_outcome_cutoff"]),
            date.fromisoformat(manifest["test_reference_start"]),
            date.fromisoformat(manifest["test_reference_end"]),
            date.fromisoformat(envelope["start_inclusive"]),
            date.fromisoformat(envelope["end_exclusive"]),
        )
        if not (
            result.price_start
            <= result.train_start
            <= result.train_cutoff
            < result.test_start
            <= result.test_end
            < result.price_end
        ):
            raise ValueError("Invalid chronological split or price envelope.")
        for day in (result.train_start, result.test_start, result.test_end):
            if (day + timedelta(days=1)).month == day.month:
                raise ValueError("Reference boundaries must be calendar month-ends.")
        return result

    def references(self) -> list[tuple[str, date, list[str]]]:
        result = []
        for split, start, end in (
            ("train", self.train_start, self.train_cutoff),
            ("test", self.test_start, self.test_end),
        ):
            for stamp in pd.date_range(start, end, freq="ME"):
                day = stamp.date()
                reasons = []
                if split == "train" and day + timedelta(days=30) > self.train_cutoff:
                    reasons.append("outcome_crosses_training_cutoff")
                result.append((split, day, reasons))
        return result


def label_snapshot(
    snapshot: MembershipSnapshot, events: SourceEventTable, split: str
) -> list[dict[str, Any]]:
    """Match REMOVE events to active record positions; retain uncertain labels."""
    if (snapshot.dataset_sha256, snapshot.coverage_start, snapshot.coverage_end) != (
        events.dataset_sha256,
        events.coverage_start,
        events.coverage_end,
    ):
        raise ValueError("Membership and events must share source and coverage.")
    reference = snapshot.as_of
    outcome = reference + timedelta(days=30)
    frame = snapshot.data
    ambiguous = set(
        frame.loc[frame.groupby("symbol").cik.transform("nunique").gt(1), "symbol"]
    )
    removals = events.data.loc[
        events.data.event_type.eq("REMOVE")
        & events.data.event_date.gt(pd.Timestamp(reference))
        & events.data.event_date.le(pd.Timestamp(outcome))
    ]
    result = []
    for (symbol, cik), group in frame.groupby(["symbol", "cik"], sort=True):
        positions = sorted(map(int, group.source_record_position))
        matched = removals.loc[removals.source_record_position.isin(positions)]
        reasons = []
        if outcome > snapshot.coverage_end:
            reasons.append("incomplete_outcome_coverage")
        if len(group) > 1:
            reasons.append("duplicate_active_identity")
        if symbol in ambiguous:
            reasons.append("multiple_active_ciks")
        if bool(group.date_added_approximate.any()):
            reasons.append("approximate_addition_date")
        if bool(group.date_removed_approximate.any()) or bool(
            matched.event_date_approximate.any()
        ):
            reasons.append("approximate_removal_date")
        if bool(matched.created_after_event.any()):
            reasons.append("created_after_removal")
        nominal = int(not matched.empty)
        result.append(
            dict(
                symbol=str(symbol),
                cik=str(cik),
                split=split,
                reference_date=reference.isoformat(),
                outcome_end=outcome.isoformat(),
                source_record_positions=positions,
                nominal_label=nominal,
                label_after_source_screen=None if reasons else nominal,
                removal_event_positions=sorted(
                    map(int, matched.source_record_position)
                ),
                source_exclusions=reasons,
            )
        )
    return result
