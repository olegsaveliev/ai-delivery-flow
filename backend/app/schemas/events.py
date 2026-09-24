"""Debate stream event contract (EPIC-B B-T1 / KAN-17, DEC-003/004/007/008).

The frozen, typed definition of every server-sent debate event. Server → client only
(DEC-004 watch-only): there are no inbound event types, and ``parse_event`` exists for
tests and server-side replay — it is not an input surface.

- ``round_completed`` fires once per round — twice per debate under DEC-003. The schema
  only enforces ``round >= 1``; the round count belongs to the orchestrator/guardrails.
- ``turn_completed`` is authoritative: emitted after the turn row is persisted (DEC-008),
  carrying its ``turn_id`` and full ``content``, which replaces any text accumulated from
  that turn's deltas (empty, discarding partial text, for a skipped turn).
- ``done`` always carries ``status: "completed"``; every failure ends with ``error``.
- ``verdict`` is a single, non-streamed event carrying the validated judge output (DEC-007).
- ``personas_assigned`` carries ``PersonaOut`` (the persisted, id-bearing shape also served
  by ``GET /api/debates/{id}``). UI color is derived client-side from ``archetype``.

Events never carry their sequence number: ``SequencedEvent`` pairs an event with its
per-debate ``seq`` (the SSE ``id:``), stamped by the sink that owns the debate's stream
(see ``app.services.events``).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.models.enums import DebateStatus, TurnStatus
from app.schemas.debate import PersonaOut
from app.schemas.verdict import Case, Tradeoff


class _StreamEvent(BaseModel):
    """Base for all stream events.

    ``extra="forbid"`` and ``frozen=True`` apply to the event's **top-level** fields only:
    unknown top-level keys are rejected and top-level fields cannot be reassigned. Nested
    models (``PersonaOut``, ``Case``) and list fields keep their own, looser semantics — they
    are not frozen and may ignore unknown nested keys. Treat emitted events as read-only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonasAssignedEvent(_StreamEvent):
    """After the 3 personas are persisted."""

    type: Literal["personas_assigned"] = "personas_assigned"
    debate_id: str = Field(..., min_length=1)
    personas: list[PersonaOut] = Field(..., min_length=1)


class TurnStartedEvent(_StreamEvent):
    """A persona turn begins."""

    type: Literal["turn_started"] = "turn_started"
    round: int = Field(..., ge=1, description="1-based round number (DEC-003: 2 rounds)")
    persona_id: str = Field(..., min_length=1)


class TurnDeltaEvent(_StreamEvent):
    """A streamed text chunk of an in-progress turn (may contain newlines/unicode)."""

    type: Literal["turn_delta"] = "turn_delta"
    round: int = Field(..., ge=1, description="1-based round number (DEC-003: 2 rounds)")
    persona_id: str = Field(..., min_length=1)
    text: str = Field(..., description="Chunk text; producers should skip empty chunks")


class TurnCompletedEvent(_StreamEvent):
    """Turn persisted (full content, authoritative — DEC-008).

    ``content`` **replaces** the streamed text: clients must discard whatever they
    accumulated from ``turn_delta`` events for this ``(round, persona_id)`` and show
    ``content`` instead. When ``status == "skipped"``, ``content`` is ``""`` and any partial
    delta text must be discarded — a turn can stream some deltas and still end skipped
    (e.g. a mid-stream failure in ``LLMService.stream_turn``, KAN-18).
    """

    type: Literal["turn_completed"] = "turn_completed"
    round: int = Field(..., ge=1, description="1-based round number (DEC-003: 2 rounds)")
    persona_id: str = Field(..., min_length=1)
    turn_id: str = Field(..., min_length=1)
    status: TurnStatus
    content: str = Field(..., description="Full persisted content; empty for skipped turns")


class RoundCompletedEvent(_StreamEvent):
    """All 3 turns of the round have landed (×2 per debate, DEC-003)."""

    type: Literal["round_completed"] = "round_completed"
    round: int = Field(..., ge=1, description="1-based round number (DEC-003: 2 rounds)")


class VerdictEvent(_StreamEvent):
    """Judge verdict persisted (DEC-007 — one non-streamed event)."""

    type: Literal["verdict"] = "verdict"
    recommendation: str = Field(..., min_length=1)
    cases: list[Case] = Field(..., min_length=1)
    tradeoffs: list[Tradeoff] = Field(..., min_length=1)


class DoneEvent(_StreamEvent):
    """Terminal: success. ``status`` is always ``completed``; failures end with ``error``."""

    type: Literal["done"] = "done"
    debate_id: str = Field(..., min_length=1)
    status: Literal[DebateStatus.COMPLETED] = DebateStatus.COMPLETED


class ErrorEvent(_StreamEvent):
    """Terminal: failure. ``code`` is open-ended; see ``KNOWN_ERROR_CODES``."""

    type: Literal["error"] = "error"
    code: str = Field(..., min_length=1)
    message: str


DebateEvent = Annotated[
    PersonasAssignedEvent
    | TurnStartedEvent
    | TurnDeltaEvent
    | TurnCompletedEvent
    | RoundCompletedEvent
    | VerdictEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

DEBATE_EVENT_ADAPTER: TypeAdapter[DebateEvent] = TypeAdapter(DebateEvent)

# The complete outbound contract, in contract-table order (DEC-004: no inbound types).
EVENT_TYPES: tuple[str, ...] = (
    "personas_assigned",
    "turn_started",
    "turn_delta",
    "turn_completed",
    "round_completed",
    "verdict",
    "done",
    "error",
)

# Exactly one of these ends every stream.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"done", "error"})

# PROVISIONAL — pending Proposed DEC-012; finalize in B-T4/B-T5. Informational only, not
# enforced (``ErrorEvent.code`` stays an open string): codes the run pipeline is expected to use.
KNOWN_ERROR_CODES: frozenset[str] = frozenset({"judge_failed", "timeout", "internal"})


def parse_event(data: str | bytes) -> DebateEvent:
    """Parse one event's JSON ``data`` back into its typed model.

    Raises ``pydantic.ValidationError`` on an unknown/missing ``type`` or an invalid payload.
    For tests and server-side replay only — not an inbound API (DEC-004).
    """
    return DEBATE_EVENT_ADAPTER.validate_json(data)


class SequencedEvent(BaseModel):
    """An event at its per-debate position; ``seq`` becomes the SSE ``id:``."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(..., ge=1)
    event: DebateEvent
