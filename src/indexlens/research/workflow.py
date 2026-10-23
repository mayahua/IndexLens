"""Build and retain the full population before comparing fixed models."""

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from indexlens.db.models import PriceSource, SourceVersion
from indexlens.db.repository import membership_at_db, source_events_db
from indexlens.features.context_review import (
    BenchmarkReview,
    benchmark_history,
    reviewed_context,
)
from indexlens.features.core import FEATURE_VERSION, calculate
from indexlens.features.query import reviewed_history
from indexlens.features.review import Review, decision_blockers
from indexlens.prices.metadata import fingerprint

from . import RESEARCH_VERSION
from .labels import Schedule, label_snapshot
from .model import (
    DEFAULT_CUTOFF,
    NINE_FEATURE_VERSION,
    NINE_FEATURES,
    NINE_RESEARCH_VERSION,
    compare,
    compare_nine,
    cutoff_limitation,
    validate_cutoff,
)


def feature_dates(schedule: Schedule) -> dict[date, date]:
    """Use the final scheduled XNYS session at or before each month-end."""
    calendar = xcals.get_calendar(
        "XNYS", start=schedule.price_start, end=schedule.price_end
    )
    return {
        day: calendar.date_to_session(day.isoformat(), direction="previous").date()
        for _, day, reasons in schedule.references()
        if not reasons
    }


def counts(rows: list[dict[str, Any]], label_key: str) -> dict[str, Any]:
    return dict(
        observations=len(rows),
        positive_observations=sum(r[label_key] == 1 for r in rows),
        negative_observations=sum(r[label_key] == 0 for r in rows),
        distinct_removal_events=len(
            {p for r in rows if r[label_key] == 1 for p in r["removal_event_positions"]}
        ),
        identities=len({(r["symbol"], r["cik"]) for r in rows}),
    )


def build_observations(
    session: Session,
    manifest: dict[str, Any],
    review: Review,
    ingestion: dict[str, Any],
    benchmark: BenchmarkReview | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    schedule = Schedule.from_manifest(manifest)
    raw = review.declarations
    if (raw["membership_sha256"], raw["data_kind"]) != (
        manifest["source_sha256"],
        manifest["data_kind"],
    ):
        raise ValueError("Manifest/review source or kind mismatch.")
    versions = session.scalars(
        select(SourceVersion).where(
            SourceVersion.dataset_sha256 == manifest["source_sha256"],
            SourceVersion.dataset_kind == manifest["data_kind"],
        )
    ).all()
    if len(versions) != 1:
        raise ValueError("Exactly one matching membership version required.")
    version = versions[0]
    if (str(version.coverage_start), str(version.coverage_end)) != (
        manifest["coverage_start"],
        manifest["coverage_end"],
    ):
        raise ValueError("Manifest coverage differs from stored membership coverage.")
    declared = {(m["symbol"], m["cik"]): m for m in manifest["identities"]}
    mappings = {(m["symbol"], m["cik"]): m for m in raw["universe"]}
    if (
        len(declared) != len(manifest["identities"])
        or len(declared) != manifest["eligible_source_identity_count"]
        or declared.keys() != mappings.keys()
    ):
        raise ValueError(
            "Review must retain the entire declared population exactly once."
        )
    ingested = {
        (r["source_identity"]["symbol"], r["source_identity"]["cik"]): r
        for r in ingestion["results"]
    }
    if (
        ingestion["dataset_id"] != raw["dataset_id"]
        or ingested.keys() != declared.keys()
        or len(ingestion["results"]) != len(declared)
    ):
        raise ValueError(
            "Ingestion report must cover the same full population/dataset."
        )
    events = source_events_db(session, version.id)
    rows = []
    skipped = []
    snapshots = []
    for split, day, reasons in schedule.references():
        if reasons:
            skipped.append(
                dict(
                    reference_date=str(day),
                    split=split,
                    reasons=reasons,
                    outcome_end=str(day + timedelta(days=30)),
                )
            )
            continue
        snapshot = membership_at_db(session, version.id, day)
        rows.extend(label_snapshot(snapshot, events, split))
        snapshots.append(
            dict(
                reference_date=str(day),
                issues=[asdict(issue) for issue in snapshot.issues],
            )
        )
    source_eligible = [r for r in rows if r["label_after_source_screen"] is not None]
    eligible_keys = {(r["symbol"], r["cik"]) for r in source_eligible}
    if eligible_keys != declared.keys():
        raise ValueError(
            "Declared population differs from recomputed source eligibility."
        )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["symbol"], row["cik"]].append(row)
    for identity, declaration in declared.items():
        positions = sorted(
            {
                p
                for r in grouped[identity]
                if r["label_after_source_screen"] is not None
                for p in r["source_record_positions"]
            }
        )
        if (
            sorted(declaration["source_record_positions"]) != positions
            or sorted(mappings[identity]["source_record_positions"]) != positions
        ):
            raise ValueError(
                "Manifest/review must pin the active eligible source records."
            )
    dates = feature_dates(schedule)
    market = (
        []
        if benchmark is None
        else benchmark_history(
            session,
            benchmark,
            schedule.price_end - timedelta(days=1),
            history_start=schedule.price_start,
        )
    )
    identity_results = []
    for identity in sorted(grouped):
        candidates = grouped[identity]
        mapping = mappings.get(identity)
        history = (
            []
            if mapping is None
            else reviewed_history(
                session,
                review,
                mapping,
                schedule.price_start,
                schedule.price_end - timedelta(days=1),
                version=version,
            )
        )
        history_days = [r.day for r in history]
        provider_result = ingested.get(identity)
        history_identity = (
            (mapping["historical_alias_of"], identity[1])
            if mapping is not None and "historical_alias_of" in mapping
            else identity
        )
        history_result = ingested.get(history_identity)
        for row in candidates:
            reference = date.fromisoformat(row["reference_date"])
            day = dates[reference]
            end = bisect_right(history_days, day)
            window = history[max(0, end - 21) : end]
            result = calculate(window, day)
            reasons = list(row["source_exclusions"])
            if mapping is None:
                reasons.append("outside_declared_source_population")
            else:
                # Keep review restrictions explicit even when no prices exist.
                reasons += decision_blockers(mapping, day, "mapping")
                reasons += decision_blockers(raw["basis_review"], day, "basis")
                if not set(row["source_record_positions"]).issubset(
                    mapping["source_record_positions"]
                ):
                    reasons.append("unreviewed_active_interval")
                if history_result is not None and history_result["status"] != "loaded":
                    reasons.append("provider_" + history_result["status"])
            reasons += [
                reason for values in result.unavailable.values() for reason in values
            ]
            row.update(
                price_history_identity=dict(
                    symbol=history_identity[0], cik=history_identity[1]
                ),
                feature_observation_date=str(day),
                latest_observed_date=str(window[-1].day) if window else None,
                features={
                    k: str(v) if v is not None else None
                    for k, v in result.values.items()
                },
                feature_unavailable=result.unavailable,
                windows=result.windows,
                input_sha256=fingerprint(result.inputs),
                input_records=[
                    dict(
                        date=i["observation_date"],
                        blockers=i["blockers"],
                        records=[
                            [p["source_id"], p["record_position"], p["record_sha256"]]
                            for p in i["provenance"]
                        ],
                    )
                    for i in result.inputs
                ],
                exclusion_reasons=sorted(set(reasons)),
                eligible=not reasons,
            )
            if benchmark is not None:
                context = reviewed_context(
                    history[max(0, end - 121) : end], market, day, review, benchmark
                )
                indicators = context["indicators"]
                row["features"].update({k: v["value"] for k, v in indicators.items()})
                row["feature_unavailable"].update(
                    {
                        k: v["unavailable"]
                        for k, v in indicators.items()
                        if v["unavailable"]
                    }
                )
                row["windows"].update(
                    {k: v["observed_dates"] for k, v in indicators.items()}
                )
                # Legacy feature gates and source/label checks still apply. Only
                # required context predictors contribute additional exclusions.
                reasons += [
                    reason
                    for f in NINE_FEATURES
                    for reason in indicators[f]["unavailable"]
                ]
                row.update(
                    context_input_sha256=context["input_sha256"],
                    context_input_records={
                        key: [
                            dict(
                                date=i["observation_date"],
                                records=[
                                    [
                                        p["source_id"],
                                        p["record_position"],
                                        p["record_sha256"],
                                    ]
                                    for p in i["provenance"]
                                ],
                            )
                            for i in items
                        ]
                        for key, items in context["inputs"].items()
                    },
                    eligible=not reasons,
                    exclusion_reasons=sorted(set(reasons)),
                )
        identity_results.append(
            dict(
                symbol=identity[0],
                cik=identity[1],
                declared=mapping is not None,
                mapping_status=mapping["status"] if mapping else "source_excluded",
                mapping_start=mapping["start"] if mapping else None,
                mapping_end=mapping["end"] if mapping else None,
                ingestion=provider_result,
                price_history_identity=dict(
                    symbol=history_identity[0], cik=history_identity[1]
                ),
                price_history_ingestion=history_result,
                candidates=len(candidates),
                eligible=sum(r["eligible"] for r in candidates),
                exclusion_reason_counts=dict(
                    Counter(
                        reason for r in candidates for reason in r["exclusion_reasons"]
                    )
                ),
            )
        )
    rows.sort(key=lambda r: (r["reference_date"], r["symbol"], r["cik"]))
    summaries = {}
    for split in ("train", "test"):
        candidates = [r for r in rows if r["split"] == split]
        screened = [r for r in candidates if r["label_after_source_screen"] is not None]
        eligible = [r for r in candidates if r["eligible"]]
        summaries[split] = dict(
            candidates=counts(candidates, "nominal_label"),
            after_source_screen=counts(screened, "label_after_source_screen"),
            eligible=counts(eligible, "label_after_source_screen"),
            excluded_observations=len(candidates) - len(eligible),
            exclusion_reason_counts=dict(
                Counter(reason for r in candidates for reason in r["exclusion_reasons"])
            ),
            excluded_positive_observations=[
                dict(
                    symbol=r["symbol"],
                    cik=r["cik"],
                    reference_date=r["reference_date"],
                    removal_event_positions=r["removal_event_positions"],
                    reasons=r["exclusion_reasons"],
                )
                for r in screened
                if r["label_after_source_screen"] == 1 and not r["eligible"]
            ],
        )
    sources = session.scalars(
        select(PriceSource)
        .where(PriceSource.dataset_id == raw["dataset_id"])
        .order_by(PriceSource.id)
    ).all()
    report = dict(
        research_version=RESEARCH_VERSION,
        feature_version=FEATURE_VERSION,
        dataset_id=raw["dataset_id"],
        data_kind=raw["data_kind"],
        source_version_id=version.id,
        membership_sha256=version.dataset_sha256,
        coverage_start=str(version.coverage_start),
        coverage_end=str(version.coverage_end),
        calendar="XNYS",
        feature_date_policy="last scheduled session <= reference_date",
        outcome_interval="(reference_date, reference_date + 30 calendar days]",
        excluded_reference_dates=skipped,
        snapshot_issues=snapshots,
        summaries=summaries,
        identities=identity_results,
        reconciliation_references=manifest.get("source_only_summaries"),
        source_catalog={
            str(s.id): dict(
                source_sha256=s.source_sha256,
                metadata_sha256=s.metadata_sha256,
                declarations=s.declarations,
                counts=s.result_counts,
            )
            for s in sources
        },
        limitations=[
            "Retrospective membership and downloaded price sources do not establish "
            "point-in-time availability; later source revisions and corporate-action "
            "adjustments may affect values. Only three price features enter models.",
            "Mapping evidence and approval bounds are retrospective assumptions; "
            "unresolved identities and dates remain unavailable.",
            "Missing acquired/delisted histories and uneven mapping evidence select "
            "the evaluated population; results do not generalize to all members.",
            "Monthly 30-day windows can count one event twice and miss other events. "
            "Repeated securities and clustered removals are dependent observations.",
            "Observed-session windows retain dated invalid rows; entirely absent "
            "interior sessions remain a separate ingestion coverage limitation.",
            "Removal probabilities are distinct from descriptive momentum ranks.",
        ],
    )
    if benchmark is not None:
        report.update(
            research_version=NINE_RESEARCH_VERSION,
            feature_version=NINE_FEATURE_VERSION,
            benchmark_review=benchmark.declarations,
            benchmark_review_sha256=benchmark.sha256,
        )
        report["limitations"][0] = (
            "Retrospective membership and downloaded prices do not establish "
            "point-in-time availability. Later revisions may affect values. "
            "Nine specified predictors enter the primary model; other indicators "
            "are supplementary descriptive context."
        )
        report["limitations"].append(
            "Feature selection followed repeated inspection of this historical "
            "test period. Matched-cohort comparisons are exploratory and do not "
            "establish independent predictive improvement. Rare removals make "
            "small Brier scores possible even for a weak constant baseline."
        )
    return rows, report


def run_research(
    session: Session,
    manifest: dict[str, Any],
    review: Review,
    ingestion: dict[str, Any],
    seed: int,
    cutoff: float = DEFAULT_CUTOFF,
    benchmark: BenchmarkReview | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = validate_cutoff(cutoff)
    rows, report = build_observations(session, manifest, review, ingestion, benchmark)
    report["evaluation"] = (
        compare(rows, seed, cutoff)
        if benchmark is None
        else compare_nine(rows, seed, cutoff)
    )
    report["limitations"].append(cutoff_limitation(cutoff))
    return rows, report
