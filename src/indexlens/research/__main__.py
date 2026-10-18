"""Save a reproducible monthly removal research run from reviewed stored inputs."""

import argparse
import gzip
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from indexlens.db.session import database_engine
from indexlens.features.review import Review
from indexlens.prices.metadata import canonical_json, fingerprint

from .workflow import run_research


def code_state(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    files = sorted((root / "src" / "indexlens").rglob("*.py"))
    files += [root / "pyproject.toml"]
    result: dict[str, Any] = dict(
        file_sha256={
            str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in files
        },
    )
    try:
        status = git("status", "--porcelain=v1", "--untracked-files=all")
        result.update(
            revision=git("rev-parse", "HEAD"),
            working_tree_status=status,
            dirty=bool(status),
        )
    except (OSError, subprocess.CalledProcessError):
        result.update(revision=None, working_tree_status=None, dirty=None)
    return result


def execute(config_path: Path, output: Path) -> dict[str, Any]:
    root = Path.cwd().resolve()
    if not output.resolve().is_relative_to(root / "data"):
        raise ValueError("Generated research outputs must be under data/.")
    if output.exists():
        raise ValueError("Choose a new output directory to preserve earlier runs.")
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    if (
        config.get("config_version") != "removal-run-v1"
        or type(config.get("seed")) is not int
    ):
        raise ValueError("A versioned configuration and integer seed are required.")
    inputs = {}
    payloads = {}
    input_bytes = {}
    for key in ("manifest", "review", "ingestion_report"):
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
                session, payloads["manifest"], review, ingestion, config["seed"]
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
    (output / "config.json").write_bytes(config_bytes)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = execute(args.config, args.output)
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
                unmet_requirements=report["evaluation"]["unmet_requirements"],
            ),
            indent=2,
        )
    )
    return 0 if report["evaluation"]["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
