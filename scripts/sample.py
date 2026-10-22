"""Build and load the public synthetic 2025–2026 sample using existing commands."""

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from indexlens.prices.metadata import PriceMetadata, fingerprint


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def command(module: str, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", module, *args], text=True, capture_output=True
    )
    if result.returncode:
        raise ValueError(f"{module} failed (exit {result.returncode}).")
    value: dict[str, Any] = json.loads(result.stdout)
    print(json.dumps(dict(command=module, result=value)), flush=True)
    return value


def prepare(output: Path) -> dict[str, Any]:
    """Create deterministic fixtures in a new directory under data/."""
    if not output.resolve().is_relative_to(Path.cwd().resolve() / "data"):
        raise ValueError("Choose an output directory under data/.")
    output.mkdir(parents=True, exist_ok=False)
    membership = output / "membership.csv"
    membership.write_text(
        "symbol,cik,name,sector,date_added,date_removed,created_at\n"
        "A,0001,Synthetic Alpha,Tech,2025-01-01,2025-10-15,2025-01-01\n"
        "A,0001,Synthetic Alpha,Tech,2026-02-01,2026-06-15,2026-02-01\n"
        "B,0002,Synthetic Beta,Tech,2025-01-01,,2025-01-01\n"
        "C,0003,Synthetic Gamma,Tech,2025-01-01*,,2025-01-01\n"
    )
    lines = ["provider_symbol,observation_date,open,high,low,close,volume"]
    for symbol in ("A", "B"):
        day, i = date(2025, 8, 1), 0
        while day <= date(2026, 8, 3):
            if day.weekday() < 5:
                value = Decimal(100 + i % 9) + Decimal(i) / (10 if symbol == "B" else 5)
                lines.append(
                    f"{symbol},{day},{value},{value},{value},{value},{100 + i}"
                )
                i += 1
            day += timedelta(days=1)
    (output / "prices.csv").write_text("\n".join(lines) + "\n")
    metadata = dict(
        dataset_id="public-synthetic-v1",
        data_kind="synthetic",
        provider="fixture",
        price_basis="synthetic_unadjusted",
        retrieved_at="2026-10-21T00:00:00+00:00",
        source_reference="indexlens-public-synthetic-v1",
        adjustment_settings=dict(auto_adjust=False, back_adjust=False),
        source_notes="Invented weekday observations for software verification.",
    )
    write_json(output / "prices.metadata.json", metadata)
    identities = [
        dict(symbol="A", cik="0001", source_record_positions=[0, 1]),
        dict(symbol="B", cik="0002", source_record_positions=[2]),
    ]
    manifest = dict(
        data_kind="synthetic",
        version="public-synthetic-v1",
        source_sha256=sha256(membership.read_bytes()).hexdigest(),
        coverage_start="2025-01-01",
        coverage_end="2026-09-30",
        train_reference_start="2025-09-30",
        train_outcome_cutoff="2025-12-31",
        test_reference_start="2026-05-31",
        test_reference_end="2026-07-31",
        label_horizon_days=30,
        price_request_envelope=dict(
            start_inclusive="2025-08-01", end_exclusive="2026-08-01"
        ),
        eligible_source_identity_count=len(identities),
        identities=identities,
    )
    decision = dict(
        status="approved",
        start="2025-08-01",
        end="2026-07-31",
        evidence=["Invented 2025–2026 sample; not real securities."],
    )
    pin = dict(
        source_sha256=sha256((output / "prices.csv").read_bytes()).hexdigest(),
        metadata_sha256=fingerprint(PriceMetadata.from_dict(metadata).declarations),
    )
    review = dict(
        review_version="feature-review-v1",
        review_id="public-synthetic-v1",
        reviewed_on="2026-10-21",
        dataset_id=metadata["dataset_id"],
        data_kind="synthetic",
        provider=metadata["provider"],
        price_basis=metadata["price_basis"],
        adjustment_settings=metadata["adjustment_settings"],
        membership_sha256=manifest["source_sha256"],
        limitations="Synthetic software example; predictive validity unestablished.",
        basis_review=decision | dict(description="Invented close/volume"),
        universe=[
            identity
            | decision
            | dict(provider_symbol=identity["symbol"], sources=[pin])
            for identity in identities
        ],
    )
    write_json(output / "manifest.json", manifest)
    write_json(output / "review.json", review)
    write_json(
        output / "ingestion.json",
        dict(
            dataset_id=metadata["dataset_id"],
            manifest_sha256=fingerprint(manifest),
            results=[dict(source_identity=i, status="loaded") for i in identities],
        ),
    )
    write_json(
        output / "research.json",
        dict(
            config_version="removal-run-v1",
            seed=42,
            manifest=str(output / "manifest.json"),
            review=str(output / "review.json"),
            ingestion_report=str(output / "ingestion.json"),
        ),
    )
    return manifest


def load(output: Path) -> None:
    prepare(output)
    membership = command(
        "indexlens.db",
        "load",
        str(output / "membership.csv"),
        "--source",
        "public-synthetic-v1",
        "--dataset-kind",
        "synthetic",
        "--coverage-start",
        "2025-01-01",
        "--coverage-end",
        "2026-09-30",
    )
    command("indexlens.db", "verify", str(membership["version_id"]))
    command(
        "indexlens.prices",
        str(output / "prices.csv"),
        "--metadata",
        str(output / "prices.metadata.json"),
        "--batch-size",
        "100",
    )
    command(
        "indexlens.research",
        "--config",
        str(output / "research.json"),
        "--output",
        str(output / "run"),
    )
    write_json(
        output / "api.json",
        dict(
            indices={
                "sample": dict(
                    version_id=membership["version_id"],
                    label="Synthetic 2025–2026 sample",
                )
            },
            reviews={"sample-review": "review.json"},
            runs={"sample-run": "run/report.json"},
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/sample"))
    args = parser.parse_args()
    try:
        load(args.output)
    except (OSError, ValueError, KeyError) as exc:
        print(
            f"Sample failed ({type(exc).__name__}); check database/migrations and "
            "choose a new directory under data/.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(dict(api_config=str(args.output / "api.json"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
