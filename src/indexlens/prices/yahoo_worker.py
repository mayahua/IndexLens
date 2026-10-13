"""One cancellable yfinance request process; saves only safe chart evidence."""

import importlib
import json
import logging
import sqlite3
import sys
import time
import warnings
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .yahoo import SETTINGS, utc_now


def pace(path: Path, interval: float) -> None:
    """Space every underlying HTTP request across all workers sharing a run."""
    with sqlite3.connect(path, timeout=60) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pacing (id INTEGER PRIMARY KEY, next REAL)"
        )
        while True:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT next FROM pacing WHERE id=1").fetchone()
            now = time.monotonic()
            delay = max(0.0, row[0] - now) if row else 0.0
            if delay > interval * 2:
                delay = 0.0  # A prior boot or a changed pacing configuration.
            if delay == 0:
                connection.execute(
                    "INSERT OR REPLACE INTO pacing VALUES (1, ?)", (now + interval,)
                )
                connection.commit()
                return
            connection.rollback()
            time.sleep(delay)


def fetch(
    request: dict[str, Any], output: Path, gate: Path, interval: float, timeout: float
) -> dict[str, Any]:
    yf = importlib.import_module("yfinance")
    http = importlib.import_module("curl_cffi.requests")
    logging.getLogger("yfinance").disabled = True
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    yf.config.network.retries = 0
    yf.config.debug.hide_exceptions = False
    receipts: list[dict[str, Any]] = []
    selected = None
    statuses: list[int] = []
    error = None
    importlib.import_module("yfinance.cache").set_cache_location(
        str(gate.parent / "session")
    )
    with http.Session(impersonate="chrome") as session:
        original_request = session.request

        def captured_request(method: str, url: str, **kwargs: Any) -> Any:
            nonlocal selected
            pace(gate, interval)
            kwargs["timeout"] = timeout
            response = original_request(method, url, **kwargs)
            status = int(response.status_code)
            statuses.append(status)
            if "/finance/chart/" in urlsplit(url).path:
                payload = response.content
                digest = sha256(payload).hexdigest()
                name = digest + ".response"
                (output / name).write_bytes(payload)
                params = kwargs.get("params") or {}
                receipt = dict(
                    file=name,
                    sha256=digest,
                    status=status,
                    received_at=utc_now(),
                    request={
                        k: params[k]
                        for k in ("period1", "period2", "interval", "range")
                        if k in params
                    },
                )
                receipts.append(receipt)
                (output / "partial.json").write_text(json.dumps(receipts))
                if status == 200 and "period1" in params and "period2" in params:
                    selected = receipt
            return response

        session.request = captured_request
        try:
            frame = yf.Ticker(request["symbol"], session=session).history(
                start=request["start"],
                end=request["end"],
                timeout=timeout,
                **SETTINGS,
            )
            # For comparison only. Canonical values come from raw chart bytes:
            # yfinance can replace missing volume with zero in its DataFrame.
            frame.to_csv(output / "yfinance.csv")
        except Exception as exc:
            error = type(exc).__name__
    transient = any(s in {429, 500, 502, 503, 504} for s in statuses)
    transient |= error in {
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectionError",
        "RequestsError",
        "DNSError",
        "YFRateLimitError",
    }
    success = selected is not None and error is None
    chart_statuses = [r["status"] for r in receipts]
    terminal_missing = 404 in chart_statuses
    if terminal_missing:
        transient = False
    failure = "http_404" if terminal_missing else error or "no_chart"
    return dict(
        status="received" if success else "failed",
        selected=selected,
        responses=receipts,
        retryable=not success and transient,
        error_code=None if success else failure,
        exception_type=error,
        library_version=yf.__version__,
        finished_at=utc_now(),
        request=request,
    )


def main() -> None:
    request_path, output_path, gate, interval, timeout = sys.argv[1:]
    output = Path(output_path)
    try:
        result = fetch(
            json.loads(Path(request_path).read_text()),
            output,
            Path(gate),
            float(interval),
            float(timeout),
        )
    except Exception as exc:
        result = dict(
            status="failed",
            error_code=type(exc).__name__,
            retryable=False,
            responses=[],
            selected=None,
            finished_at=utc_now(),
        )
    (output / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
