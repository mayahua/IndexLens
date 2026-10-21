"""Build explanation evidence and check model claims against it."""

import json
import re
from datetime import date
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .schemas import Identifier, Kind, SecurityId, iso_day
from .service import ApiError, SavedResearch, Service, decision

MAX_EVIDENCE_BYTES = 128 * 1024
MAX_RANKING_IDENTITIES = 10
QUESTIONS = {"Summarize the evidence.", "Explain the limitations."}
Date = Annotated[date, BeforeValidator(iso_day)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventTarget(StrictModel):
    kind: Literal["event"]
    index_id: Identifier
    security_id: SecurityId
    source_record_position: int = Field(ge=0, le=1_000_000, strict=True)
    event_type: Literal["ADD", "REMOVE"]
    event_date: Date


class RankingTarget(StrictModel):
    kind: Literal["ranking"]
    review_id: Identifier
    reference_date: Date
    membership: bool = Field(default=False, strict=True)


class RunTarget(StrictModel):
    kind: Literal["run"]
    run_id: Identifier


class ExplainRequest(StrictModel):
    target: Annotated[
        EventTarget | RankingTarget | RunTarget, Field(discriminator="kind")
    ]
    question: str = Field(
        default="Summarize the evidence.", min_length=1, max_length=200
    )


class Claim(StrictModel):
    reference: str = Field(
        min_length=1, max_length=500,
        description="Copy an exact reference from claim_catalog.",
    )
    value_json: str = Field(
        min_length=1, max_length=2000,
        description="Copy that catalogue entry's value_json string exactly.",
    )


class Draft(StrictModel):
    data_kind: Kind
    claims: list[Claim] = Field(
        min_length=1, max_length=12,
        description="Select evidence claims before writing prose; cite every entry.",
    )
    explanation: str = Field(
        min_length=1, max_length=4000,
        description=(
            "Short qualitative paragraph. Cite every claims entry with its exact "
            "zero-based placeholder: {{c0}}, {{c1}}, etc. All numerical facts, "
            "dates and field paths belong in claims, not prose. No digits outside "
            "placeholders, numbered lists, percentages or spelled-out numbers."
        ),
    )


class ExplanationResult(StrictModel):
    data_kind: Kind
    explanation: str
    claims: list[Claim]
    evidence: dict[str, Any]
    evidence_sha256: str
    limitations: list[str]
    validation: dict[str, bool | str]
    model: str
    provider: Literal["openai"]


def check_question(request: ExplainRequest) -> None:
    if request.question not in QUESTIONS:
        raise ApiError(
            422,
            "unsupported_question",
            "Use 'Summarize the evidence.' or 'Explain the limitations.'. "
            "Forecasts, causal explanations and investment advice are unsupported.",
        )


def event_evidence(service: Service, target: EventTarget) -> dict[str, Any]:
    # Resolve the source-scoped identity first, including its publication boundary.
    version, _ = service.security(target.security_id)
    if version.id != service.index(target.index_id).id:
        raise ApiError(
            422, "identity_version_mismatch", "Event source versions differ."
        )
    events = service.events(
        target.index_id, target.event_date, target.event_date, 200, 0
    )
    if events["next_offset"] is not None:
        raise ApiError(
            422, "evidence_too_large", "Event date exceeds explanation bounds."
        )
    matches = [
        row
        for row in events["items"]
        if row["security_id"] == target.security_id
        and row["source_record_position"] == target.source_record_position
        and row["event_type"] == target.event_type
    ]
    if len(matches) != 1:
        raise ApiError(
            404, "unknown_event", "No recorded event matches this reference."
        )
    return dict(result=matches[0], metadata=events["metadata"])


def ranking_evidence(service: Service, target: RankingTarget) -> dict[str, Any]:
    review, _ = service.review(target.review_id)
    if len(review.declarations["universe"]) > MAX_RANKING_IDENTITIES:
        raise ApiError(
            422, "evidence_too_large", "Explanations support universes of at most 10."
        )
    result = service.rankings(
        target.review_id,
        target.reference_date,
        target.membership,
        MAX_RANKING_IDENTITIES,
        0,
    )
    for row in result["items"]:
        if row["security_id"] is None:
            raise ApiError(
                422, "insufficient_evidence", "Ranking identity is unresolved."
            )
        records = service.security_detail(row["security_id"], 200, 0)
        if records["next_offset"] is not None:
            raise ApiError(422, "evidence_too_large", "Too many source intervals.")
        row["source_intervals"] = records["items"]
        mapping = next(
            m
            for m in review.declarations["universe"]
            if (m["symbol"], m["cik"]) == (row["symbol"], row["cik"])
        )
        row["mapping"] = decision(mapping) | {
            k: mapping[k] for k in ("provider_symbol", "source_record_positions")
        }
    return result


def build_evidence(
    request: ExplainRequest, research: SavedResearch, service: Service | None = None
) -> dict[str, Any]:
    check_question(request)
    target = request.target
    if isinstance(target, RunTarget):
        evidence = research.run_result(target.run_id)
        available = evidence["result"]["evaluation"]["status"] == "evaluated"
        boundary = "Predictive validity remains unestablished."
    else:
        if service is None:
            raise RuntimeError("Database service required for event or ranking.")
        if isinstance(target, EventTarget):
            evidence = event_evidence(service, target)
            available = True
            boundary = (
                "This is a recorded source event. Its dates and flags do not establish "
                "an official cause or what was known at the time."
            )
        else:
            evidence = ranking_evidence(service, target)
            available = evidence["metadata"]["ranking_complete"]
            boundary = (
                "Momentum rankings describe past returns, not removal probabilities. "
                "Observed-session coverage and reviewed price basis "
                "limit interpretation."
            )
    evidence["interpretation"] = dict(
        kind=target.kind,
        status="available" if available else "limited",
        boundary=boundary,
        data_label=(
            "Synthetic evidence for software demonstration only."
            if evidence["metadata"]["data_kind"] == "synthetic"
            else "Real source evidence with the stated coverage and limitations."
        ),
    )
    if len(canonical(evidence).encode()) > MAX_EVIDENCE_BYTES:
        raise ApiError(
            422, "evidence_too_large", "Evidence exceeds explanation bounds."
        )
    return evidence


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def scalar_references(value: Any, prefix: str = "") -> dict[str, Any]:
    """Index scalar leaves by exact JSON Pointer, including nulls and text."""
    if isinstance(value, dict):
        return {
            ref: leaf
            for key, item in value.items()
            for ref, leaf in scalar_references(
                item, prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            ).items()
        }
    if isinstance(value, list):
        return {
            ref: leaf
            for i, item in enumerate(value)
            for ref, leaf in scalar_references(item, f"{prefix}/{i}").items()
        }
    return {prefix: value}


# Numbers and dates must be inserted from checked claims, never generated in prose.
NUMERIC_PROSE = re.compile(
    r"\d|[%‰]|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|billion|trillion|half|quarter|double|twice|percent)\b",
    re.IGNORECASE,
)
TOKEN = re.compile(r"\{\{c(0|[1-9][0-9]*)\}\}")


def validate_draft(draft: Draft, evidence: dict[str, Any]) -> dict[str, Any]:
    """Check references and exact scalar claims separately from schema validity.

    Qualitative model prose is not a verified factual or causal conclusion.
    """
    if draft.data_kind != evidence["metadata"]["data_kind"]:
        raise ApiError(
            502, "invalid_evidence_reference", "Explanation data kind differs."
        )
    references = scalar_references(evidence)
    for claim in draft.claims:
        if claim.reference not in references:
            raise ApiError(
                502, "invalid_evidence_reference", "Unknown evidence reference."
            )
        # Canonical equality preserves nulls, units, precision and string identities.
        if claim.value_json != canonical(references[claim.reference]):
            raise ApiError(502, "invalid_claim", "A claim differs from its evidence.")
    tokens = {int(match.group(1)) for match in TOKEN.finditer(draft.explanation)}
    if tokens != set(range(len(draft.claims))):
        raise ApiError(
            502, "invalid_evidence_reference", "Claim citations are incomplete."
        )
    prose = TOKEN.sub("", draft.explanation)
    if "{" in prose or "}" in prose or NUMERIC_PROSE.search(prose):
        raise ApiError(502, "invalid_claim", "Numerical prose needs checked citations.")
    # Render the field path with its value so the cited measure remains explicit.
    rendered = TOKEN.sub(
        lambda m: (
            draft.claims[int(m.group(1))].reference
            + " = "
            + draft.claims[int(m.group(1))].value_json
        ),
        draft.explanation,
    )
    return dict(
        data_kind=draft.data_kind,
        explanation=rendered,
        claims=[c.model_dump() for c in draft.claims],
        evidence=evidence,
        evidence_sha256=sha256(canonical(evidence).encode()).hexdigest(),
        limitations=[
            evidence["interpretation"]["data_label"],
            evidence["interpretation"]["boundary"],
            "References and scalar values were checked against the supplied evidence. "
            "Schema and claim checks do not establish the accuracy "
            "of qualitative prose.",
        ],
        validation=dict(
            schema=True,
            references=True,
            scalar_claims=True,
            qualitative_accuracy="not_established",
        ),
    )
