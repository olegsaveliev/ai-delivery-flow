"""Persona assignment (DEC-001, DEC-002).

`assign_personas` frames each fixed archetype's stance to a specific decision and
returns the in-memory `personas_assigned` payload. Pure and deterministic: no LLM
call, no persistence, and no orchestration — those live in later tickets.
"""

from app.prompts.personas import ARCHETYPES
from app.schemas.persona import Persona


def assign_personas(decision: str, context: str | None = None) -> list[Persona]:
    """Return the three fixed personas, each with a stance framed to ``decision``.

    Deterministic given ``decision``. ``context`` is accepted for a stable call
    contract (and to seed persona turns downstream), but it does not change the
    fixed archetype set (DEC-001, DEC-002).
    """
    decision = decision.strip()
    if not decision:
        raise ValueError("decision must be a non-empty string")

    return [
        Persona(
            archetype=spec.archetype,
            name=spec.name,
            color=spec.color,
            stance=spec.frame_stance(decision),
        )
        for spec in ARCHETYPES
    ]
