"""Resumable Yahoo ingestion with bounded subprocesses and per-identity outcomes."""

import asyncio
import json
import math
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from .loading import load_price_csv
from .metadata import PriceMetadata, fingerprint
from .yahoo import (
    BASIS,
    BENCHMARKS,
    SETTINGS,
    YFINANCE_VERSION,
    YahooValidationError,
    chart_records,
    logical_csv,
    session_coverage,
    utc_now,
)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass(frozen=True)
class Options:
    concurrency: int = 2
    request_interval: float = 1.0
    request_timeout: float = 15.0
    job_timeout: float = 90.0
    retries: int = 2
    backoff: float = 2.0
    batch_size: int = 500
    offline: bool = False
    retry_failed: bool = False

    def validate(self) -> None:
        if not 1 <= self.concurrency <= 8 or not 0 <= self.retries <= 5:
            raise ValueError("Concurrency must be 1–8; retries must be 0–5.")
        values = (
            self.request_interval,
            self.request_timeout,
            self.job_timeout,
            self.backoff,
        )
        if not all(math.isfinite(v) and v > 0 for v in values) or self.batch_size < 1:
            raise ValueError(
                "Intervals, timeouts, backoff and batch size must be positive."
            )


def validate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("data_kind") != "real":
        raise ValueError("Yahoo fetching requires an explicitly real manifest.")
    if not re.fullmatch(r"[0-9a-f]{64}", raw.get("source_sha256", "")):
        raise ValueError("A pinned source fingerprint is required.")
    identities = raw.get("identities")
    if not isinstance(identities, list) or not identities:
        raise ValueError("Supply every eligible identity in the manifest.")
    if raw.get("eligible_source_identity_count") != len(identities):
        raise ValueError("Declared population differs from the manifest length.")
    envelope = raw["price_request_envelope"]
    start = date.fromisoformat(envelope["start_inclusive"])
    end = date.fromisoformat(envelope["end_exclusive"])
    if start >= end:
        raise ValueError("Start must precede the exclusive end.")
    seen = set()
    for identity in identities:
        symbol, cik = identity["symbol"], identity["cik"]
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or not isinstance(cik, str)
        ):
            raise ValueError("Each source identity requires a symbol and text CIK.")
        key = symbol, cik
        if key in seen:
            raise ValueError("Repeated source identity in manifest.")
        seen.add(key)
        candidate = identity.get("provider_symbol", symbol.replace(".", "-"))
        if not isinstance(candidate, str) or not re.fullmatch(
            r"[A-Za-z0-9^=._-]{1,40}", candidate
        ):
            raise ValueError("Invalid candidate provider symbol.")
        instrument = identity.get("instrument_type", "EQUITY")
        if instrument != "EQUITY" and BENCHMARKS.get(candidate) != instrument:
            raise ValueError("Only declared S&P benchmark instruments are supported.")
    return raw


async def run_worker(
    request: dict[str, Any], attempt: Path, gate: Path, options: Options
) -> dict[str, Any]:
    write_json(attempt / "request.json", request)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "indexlens.prices.yahoo_worker",
        str(attempt / "request.json"),
        str(attempt),
        str(gate),
        str(options.request_interval),
        str(options.request_timeout),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), options.job_timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        result = dict(
            status="failed",
            retryable=True,
            error_code="job_timeout",
            responses=[],
            selected=None,
            finished_at=utc_now(),
        )
        if isinstance(exc, asyncio.CancelledError):
            result.update(error_code="cancelled", retryable=False)
        partial = attempt / "partial.json"
        if partial.exists():
            result["responses"] = json.loads(partial.read_text())
        write_json(attempt / "receipt.json", result)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return result
    path = attempt / "receipt.json"
    if process.returncode != 0 or not path.exists():
        return dict(status="failed", retryable=False, error_code="worker_failed")
    saved_result: dict[str, Any] = json.loads(path.read_text())
    return saved_result


Worker = Callable[[dict[str, Any], Path, Path, Options], Awaitable[dict[str, Any]]]


def checked_payload(attempt: Path, receipt: dict[str, Any]) -> bytes:
    selected = receipt["selected"]
    for item in receipt["responses"]:
        if item["file"] != item["sha256"] + ".response":
            raise ValueError("Unsafe cache evidence reference.")
        payload = (attempt / item["file"]).read_bytes()
        if sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("Cached response fingerprint mismatch.")
    if selected not in receipt["responses"]:
        raise ValueError("Selected response has no transport receipt.")
    selected_path: Path = attempt / str(selected["file"])
    return selected_path.read_bytes()


async def ingest_manifest(
    engine: Engine,
    manifest: dict[str, Any],
    dataset_id: str,
    cache: Path,
    options: Options,
    *,
    worker: Worker = run_worker,
) -> dict[str, Any]:
    options.validate()
    validate_manifest(manifest)
    if not dataset_id.strip():
        raise ValueError("A stable dataset namespace is required.")
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    run_id = utc_now().replace(":", "-") + "_" + uuid4().hex[:8]
    run_dir = cache / "runs" / run_id
    run_dir.mkdir(parents=True)
    write_json(run_dir / "manifest.json", manifest)
    envelope = manifest["price_request_envelope"]
    start, end = (
        date.fromisoformat(envelope["start_inclusive"]),
        date.fromisoformat(envelope["end_exclusive"]),
    )
    report: dict[str, Any] = dict(
        run_id=run_id,
        dataset_id=dataset_id,
        manifest_sha256=fingerprint(manifest),
        requested_identities=len(manifest["identities"]),
        started_at=utc_now(),
        options=asdict(options),
        results=[],
        status="running",
    )
    write_json(run_dir / "summary.json", report)
    gate = cache / "request_pacing.sqlite3"
    locks: dict[str, asyncio.Lock] = {}
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for identity in manifest["identities"]:
        queue.put_nowait(identity)

    def request_for(identity: dict[str, Any]) -> dict[str, Any]:
        symbol = identity.get(
            "provider_symbol", identity["symbol"].replace(".", "-")
        ).upper()
        request = dict(
            symbol=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            settings=SETTINGS,
            adapter_version="yahoo-chart-v1",
            yfinance_version=YFINANCE_VERSION,
        )
        if identity.get("instrument_type", "EQUITY") != "EQUITY":
            request["instrument_type"] = identity["instrument_type"]
        return request

    async def one(identity: dict[str, Any]) -> dict[str, Any]:
        request = request_for(identity)
        symbol = request["symbol"]
        key = fingerprint(request)
        entry = cache / "requests" / key
        entry.mkdir(parents=True, exist_ok=True)
        outcome: dict[str, Any] = dict(
            source_identity=identity,
            provider_symbol=symbol,
            request_key=key,
            mapping_status="pending_historical_review",
            feature_join_approved=False,
            requested_range=envelope,
            provider_symbol_rule="explicit_candidate"
            if "provider_symbol" in identity
            else "source_symbol_dot_to_dash_candidate",
        )
        async with locks.setdefault(key, asyncio.Lock()):
            marker_path = entry / "latest.json"
            marker = (
                json.loads(marker_path.read_text()) if marker_path.exists() else None
            )
            cached = marker is not None
            if (
                marker
                and marker["status"] != "received"
                and options.retry_failed
                and not options.offline
            ):
                marker = None
                cached = False
            if marker is None:
                if options.offline:
                    return outcome | dict(
                        status="failed", error_code="cache_miss", cached=False
                    )
                for retry in range(options.retries + 1):
                    attempt = entry / uuid4().hex
                    attempt.mkdir()
                    receipt = await worker(request, attempt, gate, options)
                    marker = dict(
                        status=receipt["status"],
                        attempt=attempt.name,
                        receipt_sha256=fingerprint(receipt),
                    )
                    write_json(attempt / "receipt.json", receipt)
                    write_json(marker_path, marker)
                    if receipt["status"] == "received" or not receipt.get("retryable"):
                        break
                    if retry < options.retries:
                        await asyncio.sleep(options.backoff * 2**retry)
            assert marker is not None
            if not re.fullmatch(r"[0-9a-f]{32}", marker["attempt"]):
                raise ValueError("Invalid cache attempt reference.")
            attempt = entry / marker["attempt"]
            receipt = json.loads((attempt / "receipt.json").read_text())
            if fingerprint(receipt) != marker["receipt_sha256"]:
                raise ValueError("Cached receipt fingerprint mismatch.")
            if receipt["status"] != "received":
                return outcome | dict(
                    status="failed",
                    cached=cached,
                    error_code=(
                        "http_404"
                        if any(r["status"] == 404 for r in receipt.get("responses", []))
                        else receipt.get("error_code", "provider_failed")
                    ),
                    evidence=str(attempt.relative_to(cache)),
                )
            payload = checked_payload(attempt, receipt)
            rows, provider_metadata = chart_records(
                payload,
                symbol,
                start,
                end,
                instrument_type=request.get("instrument_type", "EQUITY"),
            )
            coverage = session_coverage(rows, start, end)
            csv_path = attempt / "logical.csv"
            csv_bytes = logical_csv(rows)
            if csv_path.exists() and csv_path.read_bytes() != csv_bytes:
                raise ValueError("Cached logical CSV differs from raw evidence.")
            if not csv_path.exists():
                csv_path.write_bytes(csv_bytes)
            declarations = dict(
                dataset_id=dataset_id,
                data_kind="real",
                provider="yahoo",
                price_basis=BASIS,
                retrieved_at=receipt["selected"]["received_at"],
                source_reference="sha256:" + receipt["selected"]["sha256"],
                adjustment_settings=SETTINGS,
                source_notes="Yahoo Finance via yfinance; personal research. "
                "Redistribution unverified; reviewed " + receipt["finished_at"][:10],
                source_identity=identity,
                source_sha256=manifest["source_sha256"],
                provider_metadata=provider_metadata,
                request=request,
                raw_evidence=receipt["responses"],
                cache_reference=str(attempt.relative_to(cache)),
                mapping_status="pending_historical_review",
                feature_join_approved=False,
            )
            metadata = PriceMetadata.from_dict(declarations)
            task = asyncio.create_task(
                asyncio.to_thread(
                    load_price_csv,
                    engine,
                    csv_path,
                    metadata,
                    batch_size=options.batch_size,
                )
            )
            try:
                loaded = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Wait for success or rollback, then preserve cancellation.
                await asyncio.gather(task, return_exceptions=True)
                raise
            return outcome | dict(
                status="loaded",
                cached=cached,
                coverage=coverage,
                load=asdict(loaded),
                evidence=str(attempt.relative_to(cache)),
            )

    async def consume() -> None:
        while not queue.empty():
            identity = queue.get_nowait()
            try:
                result: dict[str, Any] = await one(identity)
            except asyncio.CancelledError:
                result = dict(source_identity=identity, status="cancelled")
                report["results"].append(result)
                write_json(run_dir / "summary.json", report)
                raise
            except (ValueError, OSError, KeyError, TypeError, SQLAlchemyError) as exc:
                result = dict(
                    source_identity=identity,
                    status="failed",
                    error_code=exc.code
                    if isinstance(exc, YahooValidationError)
                    else type(exc).__name__,
                    feature_join_approved=False,
                    request_key=fingerprint(request_for(identity)),
                    requested_range=envelope,
                    mapping_status="pending_historical_review",
                )
            except Exception as exc:
                report["results"].append(
                    dict(
                        source_identity=identity,
                        status="failed",
                        error_code=type(exc).__name__,
                    )
                )
                raise
            finally:
                queue.task_done()
            report["results"].append(result)
            write_json(run_dir / "summary.json", report)
            print(
                json.dumps(
                    {
                        "symbol": identity["symbol"],
                        "status": result["status"],
                        "completed": len(report["results"]),
                        "total": report["requested_identities"],
                        "error_code": result.get("error_code"),
                        "counts": result.get("load", {}).get("counts"),
                    }
                ),
                flush=True,
            )

    tasks = [asyncio.create_task(consume()) for _ in range(options.concurrency)]
    group = asyncio.gather(*tasks)
    try:
        # The coordinator owns cancellation; workers receive it only once.
        await asyncio.shield(group)
        report["status"] = "completed"
    except BaseException as exc:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        cleanup = asyncio.gather(group, *tasks, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated cancellation must still wait for active loads to close.
                pass
        while not queue.empty():
            report["results"].append(
                dict(source_identity=queue.get_nowait(), status="not_attempted")
            )
            queue.task_done()
        report["status"] = (
            "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        )
        raise
    finally:
        report["finished_at"] = utc_now()
        report["loaded"] = sum(r["status"] == "loaded" for r in report["results"])
        report["failed"] = sum(r["status"] == "failed" for r in report["results"])
        write_json(run_dir / "summary.json", report)
    return report | {"report_path": str(run_dir / "summary.json")}
