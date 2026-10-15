"""Explicit, source-pinned mapping and price-basis review declarations."""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from indexlens.prices.metadata import canonical_json, fingerprint


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Review requires {key}.")
    return value


def _hash(raw: dict[str, Any], key: str) -> str:
    value = _text(raw, key)
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Review requires SHA-256 fingerprints.")
    return value


@dataclass(frozen=True)
class Review:
    declarations: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Review":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Review":
        """Validate structure, not the historical truth of a reviewer's decision."""
        if (
            not isinstance(raw, dict)
            or raw.get("review_version") != "feature-review-v1"
        ):
            raise ValueError("Unsupported review version.")
        for key in (
            "review_id",
            "dataset_id",
            "provider",
            "price_basis",
            "limitations",
        ):
            _text(raw, key)
        if raw.get("data_kind") not in {"real", "synthetic"}:
            raise ValueError("Explicit real/synthetic status required.")
        date.fromisoformat(_text(raw, "reviewed_on"))
        _hash(raw, "membership_sha256")
        if not isinstance(raw.get("adjustment_settings"), dict):
            raise ValueError("Explicit adjustment settings required.")
        basis = raw.get("basis_review")
        if not isinstance(basis, dict):
            raise ValueError("Basis review required.")
        cls._decision(basis)
        _text(basis, "description")
        universe = raw.get("universe")
        if not isinstance(universe, list) or not universe:
            raise ValueError("Declare the complete universe.")
        identities: set[tuple[str, str]] = set()
        symbols: set[str] = set()
        for mapping in universe:
            if not isinstance(mapping, dict):
                raise ValueError("Invalid universe entry.")
            identity = (_text(mapping, "symbol"), _text(mapping, "cik"))
            symbol = _text(mapping, "provider_symbol")
            if identity in identities or symbol in symbols:
                raise ValueError("Ambiguous or duplicate universe mapping.")
            identities.add(identity)
            symbols.add(symbol)
            positions = mapping.get("source_record_positions")
            if (
                not isinstance(positions, list)
                or not positions
                or any(type(p) is not int or p < 0 for p in positions)
            ):
                raise ValueError("Source record positions required.")
            cls._decision(mapping)
            sources = mapping.get("sources")
            if not isinstance(sources, list):
                raise ValueError("Source pins required.")
            if mapping["status"] == "approved" and not sources:
                raise ValueError("Approved mapping requires pinned sources.")
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError("Source pin must be an object.")
                _hash(source, "source_sha256")
                _hash(source, "metadata_sha256")
        return cls(json.loads(canonical_json(raw)))

    @staticmethod
    def _decision(decision: dict[str, Any]) -> None:
        if decision.get("status") not in {"approved", "unresolved"}:
            raise ValueError("Explicit decision status required.")
        start = date.fromisoformat(_text(decision, "start"))
        end = date.fromisoformat(_text(decision, "end"))
        if start > end:
            raise ValueError("Invalid review date range.")
        evidence = decision.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(e, str) or not e.strip() for e in evidence
        ):
            raise ValueError("Evidence references must be strings.")
        if decision["status"] == "approved" and not evidence:
            raise ValueError("Approval requires evidence.")

    @property
    def sha256(self) -> str:
        return fingerprint(self.declarations)


def decision_blockers(decision: dict[str, Any], day: date, kind: str) -> list[str]:
    if decision["status"] != "approved":
        return [f"unresolved_{kind}"]
    if (
        not date.fromisoformat(decision["start"])
        <= day
        <= date.fromisoformat(decision["end"])
    ):
        return [f"outside_{kind}_dates"]
    return []
