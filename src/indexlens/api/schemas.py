"""Request bounds, configured identifiers and response envelopes."""

import re
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

Identifier = Annotated[
    str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
]
SecurityId = Annotated[
    str, StringConstraints(pattern=r"^v[1-9][0-9]{0,9}-s(0|[1-9][0-9]{0,9})$")
]


def iso_day(value: Any) -> date:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
    ):
        raise ValueError("Use YYYY-MM-DD.")
    return date.fromisoformat(value)


Day = Annotated[
    date, BeforeValidator(iso_day), Query(description="ISO date (YYYY-MM-DD).")
]

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
Kind = Literal["real", "synthetic"]


class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=200)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indices: dict[Identifier, IndexConfig] = Field(max_length=100)
    reviews: dict[Identifier, str] = Field(default_factory=dict, max_length=100)
    runs: dict[Identifier, str] = Field(default_factory=dict, max_length=100)


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int
    next_offset: int | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    result: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedEvaluation(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    baseline: dict[str, Any] | None
    logistic: dict[str, Any] | None
    predictions: list[dict[str, Any]]
    unmet_requirements: list[str]


class SavedSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    eligible: dict[str, int]
    excluded_positive_observations: list[dict[str, Any]]


class SavedSource(BaseModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counts: dict[str, int]


class SavedInput(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SavedRun(BaseModel):
    model_config = ConfigDict(extra="allow")
    research_version: Literal["monthly-removal-v2"]
    feature_version: str
    data_kind: Kind
    dataset_id: str
    membership_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_start: date
    coverage_end: date
    evaluation: SavedEvaluation
    summaries: dict[Literal["train", "test"], SavedSummary]
    review: dict[str, Any]
    limitations: list[str]
    identities: list[dict[str, Any]]
    source_catalog: dict[str, SavedSource]
    input_files: dict[str, SavedInput] = Field(default_factory=dict)
    code: dict[str, Any] = Field(default_factory=dict)
    observation_artifact: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    snapshot_issues: list[dict[str, Any]]
    excluded_reference_dates: list[dict[str, Any]]
