"""Save a reproducible monthly removal research run from reviewed stored inputs."""

import argparse
import gzip
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from indexlens.db.session import database_engine
from indexlens.features.context_review import BenchmarkReview
from indexlens.features.review import Review
from indexlens.prices.metadata import canonical_json, fingerprint

from .model import DEFAULT_CUTOFF, validate_cutoff
from .provenance import code_state
from .workflow import run_research


def execute(
    config_path: Path, output: Path, cutoff: float | None = None
) -> dict[str, Any]:
    root = Path.cwd().resolve()
    if not output.resolve().is_relative_to(root / "data"):
        raise ValueError("Generated research outputs must be under data/.")
    if output.exists():
        raise ValueError("Choose a new output directory to preserve earlier runs.")
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    if (
        not isinstance(config, dict)
        or config.get("config_version")
        not in ("removal-run-v1", "removal-run-v2", "removal-nine-run-v1")
        or type(config.get("seed")) is not int
    ):
        raise ValueError("A versioned configuration and integer seed are required.")
    if "cutoff" in config:
        validate_cutoff(config["cutoff"])
    cutoff = validate_cutoff(
        config.get("cutoff", DEFAULT_CUTOFF) if cutoff is None else cutoff
    )
    nine = config["config_version"] == "removal-nine-run-v1"
    if nine and config["seed"] != 42:
        raise ValueError("The versioned nine-feature specification uses seed 42.")
    config = config | dict(
        config_version="removal-nine-run-v1" if nine else "removal-run-v2",
        cutoff=cutoff,
    )
    inputs = {}
    payloads = {}
    input_bytes = {}
    keys = ("manifest", "review", "ingestion_report") + (
        ("benchmark_review",) if nine else ()
    )
    for key in keys:
        path = Path(config[key])
        content = path.read_bytes()
        inputs[key] = dict(path=str(path), sha256=sha256(content).hexdigest())
        payloads[key] = json.loads(content)
        input_bytes[key] = content
    inputs["config"] = dict(
        path=str(config_path), sha256=sha256(config_bytes).hexdigest()
    )
    evidence = []
    for item in config.get("evidence_files", []):
        path = Path(item["path"])
        digest = sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Review evidence hash mismatch.")
        evidence.append(dict(path=str(path), sha256=digest))
    ingestion = payloads["ingestion_report"]
    if ingestion.get("manifest_sha256") != fingerprint(payloads["manifest"]):
        raise ValueError("Ingestion must pin the supplied population manifest content.")
    review = Review.from_dict(payloads["review"])
    benchmark = (
        BenchmarkReview.from_dict(payloads["benchmark_review"]) if nine else None
    )
    before = code_state(root)
    engine = database_engine()
    try:
        with (
            engine.connect().execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            ) as connection,
            Session(connection) as session,
            session.begin(),
        ):
            rows, report = run_research(
                session,
                payloads["manifest"],
                review,
                ingestion,
                config["seed"],
                cutoff,
                **({"benchmark": benchmark} if benchmark is not None else {}),
            )
    finally:
        engine.dispose()
    after = code_state(root)
    if before != after:
        raise ValueError(
            "Code changed during the run; rerun against a stable working tree."
        )
    report.update(
        created_at=datetime.now(timezone.utc).isoformat(),
        configuration=config,
        input_files=inputs,
        review_evidence=evidence,
        review_sha256=review.sha256,
        review=review.declarations,
        manifest=payloads["manifest"],
        code=after,
        python_version=platform.python_version(),
        package_versions={
            name: importlib.metadata.version(name)
            for name in (
                "indexlens",
                "numpy",
                "pandas",
                "scipy",
                "scikit-learn",
                "exchange-calendars",
                "SQLAlchemy",
                "psycopg",
                "yfinance",
            )
        },
    )
    output.mkdir(parents=True)
    observations = output / "observations.jsonl.gz"
    with observations.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as stream:
            for row in rows:
                stream.write((canonical_json(row) + "\n").encode())
    report["observation_artifact"] = dict(
        file=observations.name,
        rows=len(rows),
        sha256=sha256(observations.read_bytes()).hexdigest(),
    )
    for key, content in input_bytes.items():
        (output / f"{key}.json").write_bytes(content)
    (output / "config.input.json").write_bytes(config_bytes)
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "evaluate-cutoffs":
        from .cutoffs import main as cutoffs_main

        return cutoffs_main(argv[1:])
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Use evaluate-cutoffs --help to evaluate saved probabilities.",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", type=float, help="Override configuration cutoff.")
    args = parser.parse_args(argv)
    try:
        report = execute(args.config, args.output, args.cutoff)
    except SQLAlchemyError as exc:
        print(
            f"Research failed: database operation failed ({type(exc).__name__}). "
            "Check the database connection and schema.",
            file=sys.stderr,
        )
        return 1
    except KeyError as exc:
        print(f"Research failed: missing required field {exc}.", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"Research failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            dict(
                output=str(args.output),
                data_kind=report["data_kind"],
                summaries={s: r["eligible"] for s, r in report["summaries"].items()},
                evaluation_status=report["evaluation"]["status"],
                cutoff=report["evaluation"]["threshold"],
                research_version=report["research_version"],
                feature_version=report["feature_version"],
                model_version=report["evaluation"]["model_version"],
                code_revision=report["code"]["revision"],
                code_dirty=report["code"]["dirty"],
                unmet_requirements=report["evaluation"]["unmet_requirements"],
            ),
            indent=2,
        )
    )
    return 0 if report["evaluation"]["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
