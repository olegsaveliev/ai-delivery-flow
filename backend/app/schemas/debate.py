"""Debate API request/response contracts (TICKET-6 / KAN-9, DEC-004/005/008).

The HTTP surface for the debate aggregate. Response models are ORM-serializable
(``from_attributes=True``) and mirror ``models.debate.Debate`` and its children so a
persisted debate maps straight onto the wire. Kept decoupled from the internal domain
schemas (``schemas.persona.Persona`` carries a UI ``color`` the ORM row lacks) via a
dedicated ``*Out`` contract.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Archetype, DebateStatus, TurnStatus
from app.schemas.verdict import Case


class DebateCreateRequest(BaseModel):
    """Body for ``POST /api/debates`` — the decision to debate (+ optional context)."""

    decision: str = Field(..., min_length=1, description="The decision to debate")
    context: str | None = Field(default=None, description="Optional extra context")


class PersonaOut(BaseModel):
    """A persisted persona (mirrors ``models.persona.Persona``)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    archetype: Archetype
    name: str
    stance: str


class TurnOut(BaseModel):
    """A persisted turn (mirrors ``models.turn.Turn``)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    round: int
    persona_id: str
    content: str
    status: TurnStatus
    created_at: datetime


class VerdictOut(BaseModel):
    """A persisted verdict (mirrors ``models.verdict.Verdict``).

    ``cases`` is stored as ``list[dict]{option, argument}`` on the ORM row; each dict
    validates into a :class:`~app.schemas.verdict.Case`.
    """

    model_config = ConfigDict(from_attributes=True)

    recommendation: str
    cases: list[Case]
    tradeoffs: list[str]


class DebateOut(BaseModel):
    """Full debate aggregate: transcript + verdict (``GET``/``POST`` response)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: str
    context: str | None
    status: DebateStatus
    created_at: datetime
    personas: list[PersonaOut]
    turns: list[TurnOut]
    verdict: VerdictOut | None


class DebateSummary(BaseModel):
    """Lightweight history-list row — no children (``GET /api/debates``)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: str
    status: DebateStatus
    created_at: datetime
