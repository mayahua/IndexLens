"""Atomic offline CSV loads with persistent duplicate and conflict tracking."""

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from indexlens.db.models import PriceDataset, PriceObservation, PriceRow, PriceSource

from .metadata import PriceMetadata, fingerprint
from .normalization import batches, normalize_price_record


@dataclass(frozen=True)
class PriceLoadResult:
    source_id: int
    inserted: bool
    source_sha256: str
    counts: dict[str, int]


def load_price_csv(
    engine: Engine, path: Path, metadata: PriceMetadata, *, batch_size: int = 500
) -> PriceLoadResult:
    """Retain every row; commit once or roll back all writes, including conflicts.

    Dataset locks serialize loads within a namespace, including first creation.
    Input bytes are materialized for evidence; row conversion is batched. A new
    retrieval manifest adds evidence but cannot create a new observation identity.
    """
    if batch_size < 1:
        raise ValueError("Batch size must be positive.")
    # Revalidate a detached manifest, including callers that constructed it directly.
    metadata = PriceMetadata.from_dict(metadata.declarations)
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    manifest_digest = fingerprint(metadata.declarations)
    reader = csv.reader(StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    columns = next(reader, [])
    if (
        not {"provider_symbol", "observation_date"} <= set(columns)
        or len(columns) != len(set(columns))
        or any(not column.strip() for column in columns)
    ):
        raise ValueError("CSV needs unique named columns and the two observation keys.")
    with Session(engine) as session, session.begin():
        session.execute(
            pg_insert(PriceDataset)
            .values(dataset_id=metadata.dataset_id, data_kind=metadata.data_kind)
            .on_conflict_do_nothing(index_elements=["dataset_id"])
        )
        dataset = session.scalars(
            select(PriceDataset)
            .where(PriceDataset.dataset_id == metadata.dataset_id)
            .with_for_update()
        ).one()
        if dataset.data_kind != metadata.data_kind:
            raise ValueError("Dataset namespace already has a different data kind.")
        existing = session.scalar(
            select(PriceSource).where(
                PriceSource.dataset_id == metadata.dataset_id,
                PriceSource.source_sha256 == digest,
                PriceSource.metadata_sha256 == manifest_digest,
            )
        )
        if existing is not None:
            return PriceLoadResult(existing.id, False, digest, existing.result_counts)
        previous = session.scalar(
            select(PriceSource)
            .where(
                PriceSource.dataset_id == metadata.dataset_id,
                PriceSource.provider == metadata.provider,
                PriceSource.price_basis == metadata.price_basis,
            )
            .limit(1)
        )
        if previous is not None and (
            fingerprint(previous.declarations["adjustment_settings"])
            != fingerprint(metadata.adjustment_settings)
        ):
            raise ValueError("Adjustment settings changed; use a distinct price basis.")
        counts = dict(
            rows=0,
            rejected_keys=0,
            new_observations=0,
            equal_duplicates=0,
            conflicting_rows=0,
            rows_with_value_issues=0,
        )
        source = PriceSource(
            dataset_id=metadata.dataset_id,
            provider=metadata.provider,
            price_basis=metadata.price_basis,
            retrieved_at=metadata.retrieved_at,
            source_reference=metadata.source_reference,
            source_sha256=digest,
            metadata_sha256=manifest_digest,
            source_bytes=payload,
            declarations=metadata.declarations,
            result_counts=counts.copy(),
        )
        session.add(source)
        session.flush()
        for batch in batches(reader, batch_size):
            for row in batch:
                if len(row) != len(columns):
                    raise ValueError("CSV record width differs from its header.")
                fields = dict(zip(columns, row, strict=True))
                for shared in ("dataset_id", "data_kind", "provider", "price_basis"):
                    if fields.get(shared, "").strip() not in {
                        "",
                        metadata.declarations[shared],
                    }:
                        raise ValueError(
                            "CSV shared fields disagree with the manifest."
                        )
                record = normalize_price_record(fields)
                issues = list(record.issues)
                observation = None
                if not record.prices_usable or not record.volume_usable:
                    counts["rows_with_value_issues"] += 1
                if not record.has_key:
                    counts["rejected_keys"] += 1
                else:
                    observation = session.scalar(
                        select(PriceObservation).where(
                            PriceObservation.dataset_id == metadata.dataset_id,
                            PriceObservation.provider == metadata.provider,
                            PriceObservation.provider_symbol == record.provider_symbol,
                            PriceObservation.observation_date
                            == record.observation_date,
                            PriceObservation.price_basis == metadata.price_basis,
                        )
                    )
                    if observation is None:
                        observation = PriceObservation(
                            dataset_id=metadata.dataset_id,
                            provider=metadata.provider,
                            provider_symbol=record.provider_symbol,
                            observation_date=record.observation_date,
                            price_basis=metadata.price_basis,
                            normalized_values=record.values,
                            conflicted=False,
                            prices_usable=record.prices_usable,
                            volume_usable=record.volume_usable,
                        )
                        session.add(observation)
                        session.flush()
                        counts["new_observations"] += 1
                    else:
                        if observation.normalized_values != record.values:
                            observation.conflicted = True
                            counts["conflicting_rows"] += 1
                        else:
                            counts["equal_duplicates"] += 1
                        observation.prices_usable &= record.prices_usable
                        observation.volume_usable &= record.volume_usable
                    if observation.conflicted:
                        observation.prices_usable = False
                        observation.volume_usable = False
                        issues.append("conflicting_observation")
                session.add(
                    PriceRow(
                        source_id=source.id,
                        record_position=counts["rows"],
                        dataset_id=metadata.dataset_id,
                        observation_id=observation.id
                        if observation is not None
                        else None,
                        record_sha256=fingerprint(fields),
                        original_fields=fields,
                        normalized_values=record.values,
                        issues=issues,
                    )
                )
                counts["rows"] += 1
            session.flush()
            # No identity map or cross-batch Python set grows with the whole input.
            session.expunge_all()
        stored_source = session.get(PriceSource, source.id)
        assert stored_source is not None
        stored_source.result_counts = counts.copy()
        return PriceLoadResult(stored_source.id, True, digest, counts)
