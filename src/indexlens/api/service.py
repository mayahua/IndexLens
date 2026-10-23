"""Apply identity, date and publication contracts to repository results."""

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from indexlens.db.models import SourceIdentity, SourceVersion
from indexlens.features.context_review import (
    BenchmarkReview,
    benchmark_history,
    context_policy,
    reviewed_context,
)
from indexlens.features.core import FEATURE_VERSION, calculate
from indexlens.features.query import MembershipContextError
from indexlens.features.review import Review
from indexlens.prices.metadata import fingerprint
from indexlens.research.model import NINE_RESEARCH_VERSION

from .repository import Repository, issues, read_json
from .schemas import SavedRun, SecurityId, Settings

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
Sections = Literal[
    "predictions",
    "identities",
    "excluded_positives",
    "snapshot_issues",
    "sources",
    "excluded_reference_dates",
]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def page(
    items: list[dict[str, Any]],
    limit: int,
    offset: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    end = offset + limit
    return dict(
        items=items[offset:end],
        total=len(items),
        offset=offset,
        limit=limit,
        next_offset=end if end < len(items) else None,
        metadata=metadata or {},
    )


def coverage(version: SourceVersion) -> dict[str, Any]:
    return dict(
        source_version_id=version.id,
        source=version.source,
        membership_sha256=version.dataset_sha256,
        data_kind=version.dataset_kind,
        coverage_start=str(version.coverage_start),
        coverage_end=str(version.coverage_end),
    )


def date_range(start: date, end: date, maximum: int = 366) -> None:
    if start > end or (end - start).days + 1 > maximum:
        raise ApiError(
            422, "invalid_range", f"Use an ordered range of at most {maximum} days."
        )


def supported(version: SourceVersion, *days: date) -> None:
    if any(not version.coverage_start <= day <= version.coverage_end for day in days):
        raise ApiError(
            422, "outside_coverage", "Date is outside membership source coverage."
        )


def decision(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: raw[key] for key in ("status", "start", "end", "description") if key in raw
    }


def public_source(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: raw[key]
        for key in (
            "source_id",
            "source_sha256",
            "metadata_sha256",
            "record_position",
            "record_sha256",
            "retrieved_at",
            "issues",
        )
        if key in raw
    }


def public_inputs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row | dict(provenance=[public_source(p) for p in row["provenance"]])
        for row in rows
    ]


class Catalog:
    """Load operator-selected files once; request identifiers never become paths."""

    def __init__(self, settings: Settings, base: Path):
        self.settings = settings
        self.reviews: dict[str, Review] = {}
        self.benchmarks: dict[str, BenchmarkReview] = {}
        for key, filename in settings.benchmarks.items():
            self.benchmarks[key] = BenchmarkReview.from_dict(
                read_json(base / filename)[0]
            )
        self.runs: dict[str, dict[str, Any]] = {}
        self.run_hashes: dict[str, str] = {}
        for key, filename in settings.reviews.items():
            raw, _ = read_json(base / filename)
            review = Review.from_dict(raw)
            context_policy(raw)
            if len(raw["universe"]) > 1000:
                raise ValueError("An API review supports at most 1000 identities.")
            self.reviews[key] = review
        for key, filename in settings.runs.items():
            raw, digest = read_json(base / filename)
            saved = SavedRun.model_validate(raw)
            review = Review.from_dict(saved.review)
            if (
                review.declarations["data_kind"] != saved.data_kind
                or review.declarations["dataset_id"] != saved.dataset_id
                or review.declarations["membership_sha256"] != saved.membership_sha256
                or saved.coverage_start > saved.coverage_end
                or set(saved.summaries) != {"train", "test"}
            ):
                raise ValueError("Inconsistent saved research report.")
            self.runs[key], self.run_hashes[key] = raw, digest


class Service:
    def __init__(self, repository: Repository, catalog: Catalog):
        self.repo, self.catalog = repository, catalog
        self.research = SavedResearch(catalog)

    def index(self, index_id: str) -> SourceVersion:
        config = self.catalog.settings.indices.get(index_id)
        if config is None:
            raise ApiError(404, "unknown_index", "Unknown index identifier.")
        version = self.repo.version(config.version_id)
        if version is None:
            raise ApiError(
                503,
                "source_unavailable",
                "Configured membership source is unavailable.",
            )
        return version

    def indices(self, limit: int, offset: int) -> dict[str, Any]:
        return page(
            [
                dict(index_id=key, label=value.label, **coverage(self.index(key)))
                for key, value in sorted(self.catalog.settings.indices.items())
            ],
            limit,
            offset,
        )

    def security(self, security_id: SecurityId) -> tuple[SourceVersion, SourceIdentity]:
        version_text, position_text = security_id[1:].split("-s")
        version_id, position = int(version_text), int(position_text)
        if version_id not in {
            v.version_id for v in self.catalog.settings.indices.values()
        }:
            raise ApiError(404, "unknown_security", "Unknown security identifier.")
        version = self.repo.version(version_id)
        identity = self.repo.identity(version_id, position)
        if version is None or identity is None:
            raise ApiError(404, "unknown_security", "Unknown security identifier.")
        return version, identity

    def securities(self, index_id: str, limit: int, offset: int) -> dict[str, Any]:
        version = self.index(index_id)
        return page(
            [
                dict(
                    security_id=f"v{version.id}-s{row.identity_position}",
                    symbol=row.symbol,
                    cik=row.cik,
                )
                for row in self.repo.identities(version.id)
            ],
            limit,
            offset,
            coverage(version),
        )

    def security_detail(
        self, security_id: str, limit: int, offset: int
    ) -> dict[str, Any]:
        version, identity = self.security(security_id)
        return page(
            [
                row
                for row in self.repo.records(version.id).values()
                if row["security_id"] == security_id
            ],
            limit,
            offset,
            coverage(version)
            | dict(security_id=security_id, symbol=identity.symbol, cik=identity.cik),
        )

    def members(
        self, index_id: str, day: date, limit: int, offset: int
    ) -> dict[str, Any]:
        version = self.index(index_id)
        supported(version, day)
        snapshot = self.repo.membership(self.repo.session, version.id, day)
        records = self.repo.records(version.id)
        rows = [
            records[int(position)] for position in snapshot.data.source_record_position
        ]
        return page(
            rows,
            limit,
            offset,
            coverage(version)
            | dict(
                reference_date=str(day),
                issues=issues(snapshot.issues),
                interval_rule="added <= date < removed; created_at <= date",
            ),
        )

    def events(
        self, index_id: str, start: date, end: date, limit: int, offset: int
    ) -> dict[str, Any]:
        date_range(start, end)
        version = self.index(index_id)
        supported(version, start, end)
        events = self.repo.events(self.repo.session, version.id)
        records = self.repo.records(version.id)
        event_rows: Any = events.data.itertuples(index=False)
        rows = [
            records[int(row.source_record_position)]
            | dict(
                event_date=str(row.event_date.date()),
                event_type=row.event_type,
                event_date_approximate=bool(row.event_date_approximate),
                created_after_event=bool(row.created_after_event),
            )
            for row in event_rows
            if start <= row.event_date.date() <= end
        ]
        return page(
            rows,
            limit,
            offset,
            coverage(version)
            | dict(
                start=str(start),
                end=str(end),
                issues=issues(events.issues),
                query_interval="[start, end]",
                source_event_interval="(coverage_start, coverage_end]",
            ),
        )

    def review(self, review_id: str) -> tuple[Review, SourceVersion]:
        review = self.catalog.reviews.get(review_id)
        if review is None:
            raise ApiError(404, "unknown_review", "Unknown review identifier.")
        raw = review.declarations
        versions = {
            version.id: version
            for version in (self.index(key) for key in self.catalog.settings.indices)
        }
        matches = [
            v
            for v in versions.values()
            if (v.dataset_sha256, v.dataset_kind)
            == (raw["membership_sha256"], raw["data_kind"])
        ]
        if len(matches) != 1:
            raise ApiError(
                503,
                "review_unavailable",
                "Review needs one published membership source.",
            )
        return review, matches[0]

    def review_metadata(
        self, review_id: str, review: Review, version: SourceVersion
    ) -> dict[str, Any]:
        raw = review.declarations
        return coverage(version) | dict(
            review_id=review_id,
            review_sha256=review.sha256,
            dataset_id=raw["dataset_id"],
            provider=raw["provider"],
            price_basis=raw["price_basis"],
            basis_review=decision(raw["basis_review"]),
            adjustment_settings=raw["adjustment_settings"],
            limitations=raw["limitations"],
            feature_version=FEATURE_VERSION,
        )

    def reviews(self, limit: int, offset: int) -> dict[str, Any]:
        return page(
            [
                self.review_metadata(key, *self.review(key))
                | dict(
                    universe_size=len(
                        self.catalog.reviews[key].declarations["universe"]
                    )
                )
                for key in sorted(self.catalog.reviews)
            ],
            limit,
            offset,
        )

    def mapped(
        self, security_id: str, review_id: str
    ) -> tuple[Review, SourceVersion, dict[str, Any]]:
        security_version, identity = self.security(security_id)
        review, version = self.review(review_id)
        if security_version.id != version.id:
            raise ApiError(
                422,
                "identity_version_mismatch",
                "Security and review use different source versions.",
            )
        mapping = next(
            (
                m
                for m in review.declarations["universe"]
                if (m["symbol"], m["cik"]) == (identity.symbol, identity.cik)
            ),
            None,
        )
        if mapping is None:
            raise ApiError(
                422,
                "outside_review_universe",
                "Security is outside the declared review universe.",
            )
        return review, version, mapping

    def prices(
        self,
        security_id: str,
        review_id: str,
        start: date,
        end: date,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        date_range(start, end)
        review, version, mapping = self.mapped(security_id, review_id)
        history = self.repo.history(
            self.repo.session, review, mapping, start, end, version=version
        )
        values = self.repo.price_values(review, mapping["provider_symbol"], start, end)
        rows = []
        for row in history:
            # Unapproved histories remain evidence, not attributed security prices.
            blockers = list(row.blockers)
            values_row = values[str(row.day)]
            rows.append(
                dict(
                    observation_date=str(row.day),
                    **values_row,
                    attributed_to_security=not blockers,
                    unavailable=blockers,
                    close_usable=row.close_usable,
                    provenance=[public_source(p) for p in row.provenance],
                )
            )
        return page(
            rows,
            limit,
            offset,
            self.review_metadata(review_id, review, version)
            | dict(
                security_id=security_id,
                symbol=mapping["symbol"],
                cik=mapping["cik"],
                provider_symbol=mapping["provider_symbol"],
                mapping=decision(mapping),
                source_record_positions=mapping["source_record_positions"],
                start=str(start),
                end=str(end),
                empty_reason="no_observations_in_range" if not rows else None,
            ),
        )

    def features(self, security_id: str, review_id: str, day: date) -> dict[str, Any]:
        review, version, mapping = self.mapped(security_id, review_id)
        history = self.repo.history(
            self.repo.session, review, mapping, None, day, version=version
        )
        result = calculate(history, day)
        return dict(
            result=dict(
                security_id=security_id,
                symbol=mapping["symbol"],
                cik=mapping["cik"],
                features={
                    k: str(v) if v is not None else None
                    for k, v in result.values.items()
                },
                unavailable=result.unavailable,
                windows=result.windows,
                inputs=public_inputs(result.inputs),
            ),
            metadata=self.review_metadata(review_id, review, version)
            | dict(
                reference_date=str(day),
                mapping=decision(mapping),
                source_record_positions=mapping["source_record_positions"],
            ),
        )

    def financial_context(
        self,
        security_id: str,
        review_id: str,
        day: date,
        benchmark_id: str | None = None,
    ) -> dict[str, Any]:
        review, version, mapping = self.mapped(security_id, review_id)
        history = self.repo.history(
            self.repo.session,
            review,
            mapping,
            None,
            day,
            version=version,
            observation_limit=121,
        )
        policy = context_policy(review.declarations)
        benchmark = None
        market = []
        if benchmark_id is not None:
            benchmark = self.catalog.benchmarks.get(benchmark_id)
            if benchmark is None:
                raise ApiError(404, "unknown_benchmark", "Unknown benchmark review.")
            if benchmark.declarations["data_kind"] != review.declarations["data_kind"]:
                raise ApiError(
                    422, "data_kind_mismatch", "Benchmark data kind differs."
                )
            market = benchmark_history(self.repo.session, benchmark, day)
        liquidity = policy.get("dollar_volume_review")
        result = reviewed_context(history, market, day, review, benchmark)
        result.update(
            security_id=security_id,
            symbol=mapping["symbol"],
            cik=mapping["cik"],
            research_input_sha256=result["input_sha256"],
        )
        result["inputs"] = {k: public_inputs(v) for k, v in result["inputs"].items()}
        result["input_sha256"] = fingerprint(result["inputs"])
        benchmark_metadata = None
        if benchmark is not None:
            raw = benchmark.declarations
            benchmark_metadata = {
                k: raw[k]
                for k in (
                    "dataset_id",
                    "data_kind",
                    "provider",
                    "price_basis",
                    "adjustment_settings",
                    "currency",
                    "return_convention",
                    "limitations",
                )
            }
            benchmark_metadata.update(
                benchmark_id=benchmark_id,
                review_sha256=benchmark.sha256,
                identity=decision(raw["benchmark"])
                | {
                    k: raw["benchmark"][k]
                    for k in ("provider_symbol", "instrument_type")
                },
                basis_review=decision(raw["basis_review"]),
            )
        return dict(
            result=result,
            metadata=self.review_metadata(review_id, review, version)
            | dict(
                reference_date=str(day),
                provider_symbol=mapping["provider_symbol"],
                mapping=decision(mapping),
                source_record_positions=mapping["source_record_positions"],
                financial_context_policy={
                    k: v for k, v in policy.items() if k != "dollar_volume_review"
                },
                dollar_volume_review=decision(liquidity) if liquidity else None,
                benchmark=benchmark_metadata,
            ),
        )

    def rankings(
        self, review_id: str, day: date, membership: bool, limit: int, offset: int
    ) -> dict[str, Any]:
        review, version = self.review(review_id)
        if membership:
            supported(version, day)
        try:
            result = self.repo.features(
                self.repo.session, review, day, membership=membership
            )
        except MembershipContextError as exc:
            raise ApiError(
                422,
                "invalid_membership_context",
                "Membership context requires a common observation date.",
            ) from exc
        ids = {
            (i.symbol, i.cik): f"v{version.id}-s{i.identity_position}"
            for i in self.repo.identities(version.id)
        }
        rows = [
            dict(
                row,
                security_id=ids.get((row["symbol"], row["cik"])),
                inputs=public_inputs(row["inputs"]),
            )
            for row in result["results"]
        ]
        metadata = self.review_metadata(review_id, review, version) | {
            k: result[k]
            for k in (
                "description",
                "observation_date",
                "common_observation_date",
                "ranking_complete",
                "membership_context",
            )
        }
        metadata["universe_size"] = len(rows)
        return page(rows, limit, offset, metadata)

    def run_records(
        self, run_id: str, section: Sections, limit: int, offset: int
    ) -> dict[str, Any]:
        raw = self.research.run(run_id)
        if section == "predictions":
            rows = raw["evaluation"]["predictions"]
        elif section == "sources":
            rows = [
                dict(
                    source_id=key,
                    source_sha256=value["source_sha256"],
                    metadata_sha256=value["metadata_sha256"],
                    counts=value["counts"],
                )
                for key, value in raw["source_catalog"].items()
            ]
        elif section == "excluded_positives":
            rows = [
                dict(row, split=split)
                for split, summary in raw["summaries"].items()
                for row in summary["excluded_positive_observations"]
            ]
        else:
            rows = raw[section]
        # Saved IDs may belong to another database. Resolve against hash + kind.
        versions = [self.index(key) for key in self.catalog.settings.indices]
        matches = {
            v.id: v
            for v in versions
            if (v.dataset_sha256, v.dataset_kind)
            == (raw["membership_sha256"], raw["data_kind"])
        }
        ids = {}
        if len(matches) == 1:
            version = next(iter(matches.values()))
            ids = {
                (i.symbol, i.cik): f"v{version.id}-s{i.identity_position}"
                for i in self.repo.identities(version.id)
            }
        response = page(
            rows,
            limit,
            offset,
            self.research.run_metadata(run_id) | dict(section=section),
        )
        response["items"] = [
            dict(row, security_id=ids.get((row["symbol"], row["cik"])))
            if "symbol" in row and "cik" in row
            else row
            for row in response["items"]
        ]
        return response


class SavedResearch:
    """Serve the configured snapshot of saved research results."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def run(self, run_id: str) -> dict[str, Any]:
        raw = self.catalog.runs.get(run_id)
        if raw is None:
            raise ApiError(404, "unknown_run", "Unknown research run identifier.")
        return raw

    def run_metadata(self, run_id: str) -> dict[str, Any]:
        raw = self.run(run_id)
        return dict(
            run_id=run_id,
            report_sha256=self.catalog.run_hashes[run_id],
            threshold=raw["evaluation"].get("threshold"),
            **{
                k: raw[k]
                for k in (
                    "data_kind",
                    "dataset_id",
                    "research_version",
                    "feature_version",
                    "membership_sha256",
                    "coverage_start",
                    "coverage_end",
                )
            },
        )

    def runs(self, limit: int, offset: int) -> dict[str, Any]:
        return page(
            [
                self.run_metadata(key) | dict(status=raw["evaluation"]["status"])
                for key, raw in sorted(self.catalog.runs.items())
            ],
            limit,
            offset,
        )

    def run_result(self, run_id: str) -> dict[str, Any]:
        raw = self.run(run_id)
        result: dict[str, Any] = {
            k: raw[k]
            for k in (
                "created_at",
                "calendar",
                "feature_date_policy",
                "outcome_interval",
                "limitations",
                "review_sha256",
                "python_version",
                "package_versions",
                "reconciliation_references",
            )
            if k in raw
        }
        result["limitations"] = list(raw["limitations"]) + [
            "Predictive validity remains unestablished."
        ]
        result["evaluation"] = {
            k: v for k, v in raw["evaluation"].items() if k != "predictions"
        }
        if raw["research_version"] == NINE_RESEARCH_VERSION:
            result["model_labels"] = dict(
                baseline="Constant-probability baseline",
                matched_three="Three-feature logistic regression",
                logistic="Nine-feature logistic regression",
            )
        result["summaries"] = {
            split: {
                k: v
                for k, v in summary.items()
                if k != "excluded_positive_observations"
            }
            for split, summary in raw["summaries"].items()
        }
        result["input_sha256"] = {
            key: value["sha256"] for key, value in raw.get("input_files", {}).items()
        }
        result["code"] = {
            key: raw.get("code", {}).get(key)
            for key in ("revision", "dirty", "file_sha256")
        }
        result["observation_artifact"] = {
            k: v for k, v in raw.get("observation_artifact", {}).items() if k != "file"
        }
        review = raw["review"]
        result["price_basis"] = review["price_basis"]
        result["basis_review"] = decision(review["basis_review"])
        result["adjustment_settings"] = review["adjustment_settings"]
        result["review_limitations"] = review["limitations"]
        manifest = raw.get("manifest", {})
        result["split"] = {
            k: manifest[k]
            for k in (
                "train_reference_start",
                "train_outcome_cutoff",
                "test_reference_start",
                "test_reference_end",
                "label_horizon_days",
                "price_request_envelope",
            )
            if k in manifest
        }
        return dict(result=result, metadata=self.run_metadata(run_id))


def bounded(payload: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(payload, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        raise ApiError(
            422, "response_too_large", "Reduce limit or narrow the requested range."
        )
    return payload
