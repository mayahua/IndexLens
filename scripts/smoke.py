"""Check the public synthetic sample over HTTP, including unavailable OpenAI."""

import argparse
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def check(base: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def request(
        path: str, status: int = 200, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        req = Request(
            base.rstrip("/") + "/api/v1" + path,
            data=json.dumps(payload).encode() if payload else None,
            headers={"Content-Type": "application/json"} if payload else {},
        )
        try:
            response = urlopen(req, timeout=20)
        except HTTPError as exc:
            response = exc
        with response:
            value: dict[str, Any] = json.load(response)
            if response.status != status:
                raise ValueError(f"Unexpected status for {path}: {response.status}")
        results.append(dict(path=path, status=status))
        return value

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    require(request("/health")["status"] == "ok", "Health failed")
    indices = request("/indices")["items"]
    require(indices[0]["data_kind"] == "synthetic", "Incorrect source label")
    securities = request("/indices/sample/securities")["items"]
    security = next(row["security_id"] for row in securities if row["symbol"] == "A")
    require(request(f"/securities/{security}")["total"] == 2, "Intervals lost")
    request("/indices/sample/membership?as_of=2026-05-29")
    events = request("/indices/sample/events?start=2026-06-01&end=2026-06-30")
    require(events["total"] == 1, "Expected synthetic removal")
    request("/reviews")
    price = request(
        f"/securities/{security}/prices?review_id=sample-review"
        "&start=2026-05-01&end=2026-05-29"
    )
    require(price["total"] == 21, "Unexpected price count")
    request(
        f"/securities/{security}/features?review_id=sample-review"
        "&reference_date=2026-05-29"
    )
    ranking = request(
        "/rankings?review_id=sample-review&reference_date=2026-05-29&membership=true"
    )
    require(ranking["total"] == 2, "Unexpected ranking universe")
    request("/research/runs")
    run = request("/research/runs/sample-run")
    require(run["metadata"]["data_kind"] == "synthetic", "Incorrect run label")
    records = request("/research/runs/sample-run/records?section=predictions")
    require(records["total"] == 4, "Unexpected test observation count")
    empty = request(
        f"/securities/{security}/prices?review_id=sample-review"
        "&start=2026-05-30&end=2026-05-31"
    )
    require(empty["total"] == 0, "Empty range changed")
    request("/indices/missing/securities", 404)
    request("/indices/sample/membership?as_of=2026-12-01", 422)
    request("/indices?limit=201", 422)
    request(
        "/research/explain",
        422,
        dict(
            target=dict(kind="run", run_id="sample-run"), question="Predict removals."
        ),
    )
    missing = request(
        "/research/explain", 503, dict(target=dict(kind="run", run_id="sample-run"))
    )
    require(missing["error"]["code"] == "openai_not_configured", "Provider status")
    request("/health")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    print(json.dumps(check(args.base_url), indent=2))
