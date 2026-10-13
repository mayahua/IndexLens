"""Tests for resumable ingestion, retries, cancellation, pacing and full scope."""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine

from indexlens.prices.ingestion import Options, ingest_manifest, run_worker
from indexlens.prices.loading import PriceLoadResult
from indexlens.prices.yahoo_worker import pace
from tests.test_database import counts
from tests.test_yahoo import FIXTURE, manifest


def response(
    request: dict[str, Any],
    attempt: Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is None:
        payload = json.loads(FIXTURE.read_bytes())
    payload["chart"]["result"][0]["meta"]["symbol"] = request["symbol"]
    data = json.dumps(payload).encode()
    digest = sha256(data).hexdigest()
    (attempt / (digest + ".response")).write_bytes(data)
    selected = dict(
        file=digest + ".response",
        sha256=digest,
        status=200,
        received_at="2026-10-12T12:00:00+00:00",
    )
    return dict(
        status="received",
        selected=selected,
        responses=[selected],
        request=request,
        retryable=False,
        finished_at="2026-10-12T12:00:00+00:00",
    )


@pytest.mark.postgres
def test_cache_resume_database_parity_and_tamper_rejection(
    database: Engine, tmp_path: Path
) -> None:
    calls = 0

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return response(request, attempt)

    async def run() -> None:
        first = await ingest_manifest(
            database,
            manifest(2),
            "mock-real-namespace",
            tmp_path,
            Options(),
            worker=worker,
        )
        assert first["loaded"] == 2 and calls == 2
        before = counts(database)
        repeat = await ingest_manifest(
            database,
            manifest(2),
            "mock-real-namespace",
            tmp_path,
            Options(offline=True),
            worker=worker,
        )
        assert repeat["loaded"] == 2 and calls == 2
        assert all(r["cached"] and not r["load"]["inserted"] for r in repeat["results"])
        assert counts(database) == before
        raw = next((tmp_path / "requests").rglob("*.response"))
        raw.write_bytes(b"changed")
        broken = await ingest_manifest(
            database,
            manifest(2),
            "mock-real-namespace",
            tmp_path,
            Options(offline=True),
            worker=worker,
        )
        assert broken["failed"] == 1 and calls == 2
        assert counts(database) == before

    asyncio.run(run())


def test_retry_failure_cache_offline_and_all_580_identities(tmp_path: Path) -> None:
    calls = 0
    active = 0
    peak = 0

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal calls, active, peak
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        return dict(status="failed", error_code="http_404", retryable=False)

    engine = create_engine("sqlite://")  # Failed fetches must never touch storage.

    async def run() -> None:
        result = await ingest_manifest(
            engine,
            manifest(580),
            "all-identities",
            tmp_path,
            Options(concurrency=3),
            worker=worker,
        )
        assert result["failed"] == calls == 580
        assert len(result["results"]) == 580 and peak <= 3
        repeat = await ingest_manifest(
            engine, manifest(580), "all-identities", tmp_path, Options(), worker=worker
        )
        assert repeat["failed"] == 580 and calls == 580
        await ingest_manifest(
            engine,
            manifest(1),
            "all-identities",
            tmp_path,
            Options(retry_failed=True),
            worker=worker,
        )
        assert calls == 581
        offline = await ingest_manifest(
            engine,
            manifest(581),
            "all-identities",
            tmp_path,
            Options(offline=True),
            worker=worker,
        )
        assert offline["failed"] == 581 and calls == 581

    asyncio.run(run())
    engine.dispose()


def test_transient_retries_are_bounded(tmp_path: Path) -> None:
    calls = 0

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return dict(status="failed", error_code="http_503", retryable=True)

    engine = create_engine("sqlite://")
    result = asyncio.run(
        ingest_manifest(
            engine,
            manifest(),
            "transient",
            tmp_path,
            Options(retries=2, backoff=0.001),
            worker=worker,
        )
    )
    assert calls == 3 and result["failed"] == 1
    assert len(list((tmp_path / "requests").rglob("receipt.json"))) == 3
    engine.dispose()


def test_cancellation_records_inflight_and_unattempted_identities(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    closed = 0

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal closed
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            closed += 1
        return {}

    engine = create_engine("sqlite://")

    async def run() -> None:
        task = asyncio.create_task(
            ingest_manifest(
                engine, manifest(5), "cancel", tmp_path, Options(), worker=worker
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    report = json.loads(next((tmp_path / "runs").rglob("summary.json")).read_text())
    assert report["status"] == "cancelled" and closed == 2
    assert len(report["results"]) == 5
    assert sum(r["status"] == "not_attempted" for r in report["results"]) == 3
    engine.dispose()


@pytest.mark.parametrize("cancel", [False, True])
def test_worker_timeout_and_cancellation_reap_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    original = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []
    started = asyncio.Event()

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await original(
            sys.executable, "-c", "import time; time.sleep(30)", **kwargs
        )
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run() -> None:
        task = asyncio.create_task(
            run_worker({}, tmp_path, tmp_path / "gate", Options(job_timeout=0.05))
        )
        await started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task)["error_code"] == "job_timeout"

    asyncio.run(run())
    assert processes[0].returncode is not None


def test_pacing_is_shared_independently_of_concurrency(tmp_path: Path) -> None:
    def request(_: int) -> float:
        pace(tmp_path / "pacing.db", 0.06)
        return time.monotonic()

    with ThreadPoolExecutor(max_workers=3) as pool:
        starts = sorted(pool.map(request, range(3)))
    assert all(b - a >= 0.05 for a, b in zip(starts, starts[1:], strict=False))


@pytest.mark.postgres
def test_transient_failure_then_success_keeps_both_attempts(
    database: Engine,
    tmp_path: Path,
) -> None:
    calls = 0

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return dict(status="failed", error_code="http_429", retryable=True)
        return response(request, attempt)

    report = asyncio.run(
        ingest_manifest(
            database,
            manifest(),
            "recovered",
            tmp_path,
            Options(retries=1, backoff=0.001),
            worker=worker,
        )
    )
    assert report["loaded"] == 1 and calls == 2
    assert len(list((tmp_path / "requests").rglob("receipt.json"))) == 2


def test_unexpected_failure_preserves_every_identity(tmp_path: Path) -> None:
    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        raise RuntimeError("simulated worker failure")

    engine = create_engine("sqlite://")
    with pytest.raises(RuntimeError):
        asyncio.run(
            ingest_manifest(
                engine,
                manifest(5),
                "failed",
                tmp_path,
                Options(concurrency=1),
                worker=worker,
            )
        )
    report = json.loads(next((tmp_path / "runs").rglob("summary.json")).read_text())
    assert report["status"] == "failed"
    assert len(report["results"]) == 5
    assert report["results"][0]["error_code"] == "RuntimeError"
    engine.dispose()


@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("load_fails", [False, True])
def test_cancellation_waits_for_active_load_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    repeat_cancel: bool,
    load_fails: bool,
) -> None:
    engine = create_engine("sqlite://")
    release = Event()
    closed = Event()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        sibling_cancelled = asyncio.Event()

        def slow_load(*args: Any, **kwargs: Any) -> PriceLoadResult:
            loop.call_soon_threadsafe(started.set)
            try:
                if not release.wait(timeout=5):
                    raise TimeoutError("Test did not release the load.")
                if load_fails:
                    raise ValueError("Simulated rollback after cancellation.")
                return PriceLoadResult(1, True, "a" * 64, {})
            finally:
                closed.set()

        async def worker(
            request: dict[str, Any], attempt: Path, gate: Path, options: Options
        ) -> dict[str, Any]:
            if request["symbol"] == "DEMO_0":
                return response(request, attempt)
            if request["symbol"] == "DEMO_2":
                raise AssertionError("Queued work started after cancellation.")
            try:
                await asyncio.sleep(30)
            finally:
                sibling_cancelled.set()
            return {}

        monkeypatch.setattr("indexlens.prices.ingestion.load_price_csv", slow_load)
        task = asyncio.create_task(
            ingest_manifest(
                engine, manifest(3), "cancel-load", tmp_path, Options(), worker=worker
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            await asyncio.wait_for(sibling_cancelled.wait(), timeout=5)
            done, _ = await asyncio.wait({task}, timeout=0.05)
            assert not done and not closed.is_set()
            if repeat_cancel:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.05)
                assert not done and not closed.is_set()
            summary = next((tmp_path / "runs").rglob("summary.json"))
            assert json.loads(summary.read_text())["status"] == "running"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set()
            report = json.loads(summary.read_text())
            assert report["status"] == "cancelled"
            statuses = {
                result["source_identity"]["symbol"]: result["status"]
                for result in report["results"]
            }
            assert statuses == {
                "DEMO_0": "cancelled",
                "DEMO_1": "cancelled",
                "DEMO_2": "not_attempted",
            }
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
        assert "exception was never retrieved" not in caplog.text
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_malformed_chart_only_fails_its_identity_and_stays_cached(
    database: Engine,
    tmp_path: Path,
) -> None:
    calls = 0
    malformed = json.loads(FIXTURE.read_bytes())
    malformed["chart"]["result"][0]["indicators"]["quote"] = [None]

    async def worker(
        request: dict[str, Any], attempt: Path, gate: Path, options: Options
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return response(
            request, attempt, malformed if request["symbol"] == "DEMO_0" else None
        )

    async def run() -> None:
        for offline in (False, True):
            report = await ingest_manifest(
                database,
                manifest(3),
                "malformed-chart",
                tmp_path,
                Options(concurrency=1, offline=offline),
                worker=worker,
            )
            assert report["status"] == "completed"
            assert report["loaded"] == 2 and report["failed"] == 1 and calls == 3
            failed = report["results"][0]
            assert failed["source_identity"]["symbol"] == "DEMO_0"
            assert failed["error_code"] == "chart_shape_invalid"
            assert not failed["feature_join_approved"]
            assert counts(database)["price_sources"] == 2
            assert counts(database)["price_observations"] == 6
            assert counts(database)["price_rows"] == 6
            if offline:
                assert all(
                    not result["load"]["inserted"] for result in report["results"][1:]
                )
        assert len(list((tmp_path / "requests").rglob("*.response"))) == 3

    asyncio.run(run())
