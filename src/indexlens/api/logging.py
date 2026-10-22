"""Safe request summaries using route templates and fixed error categories."""

import json
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("uvicorn.error")


async def request_summary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Exception strings may contain database URLs or input payloads.
        response = JSONResponse(
            status_code=500,
            content=dict(error=dict(code="internal_error", message="Request failed.")),
        )
    route = getattr(request.scope.get("route"), "path", "unmatched")
    method = (
        request.method
        if request.method in {"GET", "POST", "HEAD", "OPTIONS"}
        else "other"
    )
    logger.info(
        json.dumps(
            dict(
                event="request",
                method=method,
                route=route,
                status=response.status_code,
                elapsed_ms=round((perf_counter() - started) * 1000, 3),
            )
        )
    )
    return response
