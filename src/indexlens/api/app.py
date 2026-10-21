"""Public API routes for IndexLens data and research results."""

import os
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from indexlens.db.session import database_engine
from indexlens.features.query import EvidenceUnavailableError

from . import openai_provider
from .explanation import (
    ExplainRequest,
    ExplanationResult,
    RunTarget,
    build_evidence,
    check_question,
)
from .repository import Repository, read_json
from .request_limits import ExplainBodyLimit
from .schemas import Day, Identifier, Limit, Offset, Page, Result, SecurityId, Settings
from .service import ApiError, Catalog, SavedResearch, Sections, Service, bounded


def create_app(
    config_path: Path | None = None, engine: Engine | None = None
) -> FastAPI:
    """Create an application from operator configuration; no data writes occur."""
    if config_path is None:
        configured = os.environ.get("INDEXLENS_API_CONFIG")
        if not configured:
            raise ValueError("Set INDEXLENS_API_CONFIG to an API configuration file.")
        config_path = Path(configured)
    settings = Settings.model_validate(read_json(config_path, 1024 * 1024)[0])
    catalog = Catalog(settings, config_path.resolve().parent)
    research = SavedResearch(catalog)
    owned_engine = engine is None
    db = engine if engine is not None else database_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            if owned_engine:
                db.dispose()

    app = FastAPI(title="IndexLens API", version="1.0", lifespan=lifespan)
    app.add_middleware(ExplainBodyLimit)

    @contextmanager
    def access() -> Generator[Service, None, None]:
        """Open a separate read-only database session for each request."""
        try:
            with (
                db.connect().execution_options(
                    isolation_level="REPEATABLE READ",
                    postgresql_readonly=True,
                ) as connection,
                Session(connection) as session,
                session.begin(),
            ):
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                yield Service(Repository(session), catalog)
        except SQLAlchemyError as exc:
            raise ApiError(
                503, "database_unavailable", "Database is unavailable."
            ) from exc
        except EvidenceUnavailableError as exc:
            raise ApiError(
                503,
                "evidence_unavailable",
                "Configured evidence is unavailable or inconsistent.",
            ) from exc

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=dict(error=dict(code=exc.code, message=exc.message)),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=dict(
                error=dict(
                    code="invalid_request",
                    message="Check parameter types and documented bounds.",
                    fields=[list(error["loc"]) for error in exc.errors()],
                )
            ),
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        with access() as service:
            service.repo.ready()
        return dict(status="ok")

    @app.get("/api/v1/indices", response_model=Page)
    def indices(limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        with access() as service:
            return bounded(service.indices(limit, offset))

    @app.get("/api/v1/indices/{index_id}/securities", response_model=Page)
    def securities(
        index_id: Identifier, limit: Limit = 100, offset: Offset = 0
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.securities(index_id, limit, offset))

    @app.get("/api/v1/securities/{security_id}", response_model=Page)
    def security(
        security_id: SecurityId, limit: Limit = 100, offset: Offset = 0
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.security_detail(security_id, limit, offset))

    @app.get("/api/v1/indices/{index_id}/membership", response_model=Page)
    def members(
        index_id: Identifier, as_of: Day, limit: Limit = 100, offset: Offset = 0
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.members(index_id, as_of, limit, offset))

    @app.get("/api/v1/indices/{index_id}/events", response_model=Page)
    def events(
        index_id: Identifier,
        start: Day,
        end: Day,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.events(index_id, start, end, limit, offset))

    @app.get("/api/v1/reviews", response_model=Page)
    def reviews(limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        with access() as service:
            return bounded(service.reviews(limit, offset))

    @app.get("/api/v1/securities/{security_id}/prices", response_model=Page)
    def prices(
        security_id: SecurityId,
        review_id: Identifier,
        start: Day,
        end: Day,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(
                service.prices(security_id, review_id, start, end, limit, offset)
            )

    @app.get("/api/v1/securities/{security_id}/features", response_model=Result)
    def features(
        security_id: SecurityId, review_id: Identifier, reference_date: Day
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.features(security_id, review_id, reference_date))

    @app.get("/api/v1/rankings", response_model=Page)
    def rankings(
        review_id: Identifier,
        reference_date: Day,
        membership: bool = False,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(
                service.rankings(review_id, reference_date, membership, limit, offset)
            )

    @app.get("/api/v1/research/runs", response_model=Page)
    def runs(limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        return bounded(research.runs(limit, offset))

    def explanation_evidence(payload: ExplainRequest) -> dict[str, Any]:
        if isinstance(payload.target, RunTarget):
            return build_evidence(payload, research)
        with access() as service:
            return build_evidence(payload, research, service)

    @app.post("/api/v1/research/explain", response_model=ExplanationResult)
    async def explain(payload: ExplainRequest) -> dict[str, Any]:
        check_question(payload)
        evidence = await run_in_threadpool(explanation_evidence, payload)
        # Release the read transaction before waiting for the remote provider.
        return bounded(await openai_provider.explain(payload, evidence))

    @app.get("/api/v1/research/runs/{run_id}", response_model=Result)
    def run(run_id: Identifier) -> dict[str, Any]:
        return bounded(research.run_result(run_id))

    @app.get("/api/v1/research/runs/{run_id}/records", response_model=Page)
    def records(
        run_id: Identifier, section: Sections, limit: Limit = 100, offset: Offset = 0
    ) -> dict[str, Any]:
        with access() as service:
            return bounded(service.run_records(run_id, section, limit, offset))

    return app
