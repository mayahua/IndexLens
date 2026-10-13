"""Explicit declarations accompanying saved price input bytes."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any


def fingerprint(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class PriceMetadata:
    dataset_id: str
    data_kind: str
    provider: str
    price_basis: str
    retrieved_at: datetime
    source_reference: str
    adjustment_settings: dict[str, Any]
    source_notes: str
    declarations: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PriceMetadata":
        """Validate a manifest and retain additional provenance declarations."""
        strings: dict[str, str] = {}
        for key in (
            "dataset_id",
            "data_kind",
            "provider",
            "price_basis",
            "retrieved_at",
            "source_reference",
            "source_notes",
        ):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Metadata requires a nonempty {key}.")
            strings[key] = value.strip()
        if strings["data_kind"] not in {"real", "synthetic"}:
            raise ValueError("Data kind must be real or synthetic.")
        retrieved = datetime.fromisoformat(strings["retrieved_at"])
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise ValueError("Retrieval time must include a timezone.")
        settings = raw.get("adjustment_settings")
        if not isinstance(settings, dict):
            raise ValueError("Explicit adjustment_settings object is required.")
        # Round-trip to detach mutable input and reject non-JSON/nonfinite values.
        declarations = json.loads(canonical_json(raw))
        retrieved = retrieved.astimezone(timezone.utc)
        if declarations.get("normalization_version", "price-v1") != "price-v1":
            raise ValueError("Unsupported normalization version.")
        declarations["normalization_version"] = "price-v1"
        declarations.update(strings)
        declarations["retrieved_at"] = retrieved.isoformat()
        return cls(
            strings["dataset_id"],
            strings["data_kind"],
            strings["provider"],
            strings["price_basis"],
            retrieved,
            strings["source_reference"],
            declarations["adjustment_settings"],
            strings["source_notes"],
            declarations,
        )
