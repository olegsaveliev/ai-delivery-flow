from enum import Enum

from pydantic import BaseModel, Field


class Archetype(str, Enum):
    """The three fixed persona archetypes (DEC-001, DEC-002)."""

    ADVOCATE = "advocate"
    SKEPTIC = "skeptic"
    PRAGMATIST = "pragmatist"


class Persona(BaseModel):
    """A persona assigned to a specific decision — the `personas_assigned` payload.

    In-memory only. TICKET-2 does no persistence; the data layer (TICKET-1)
    assigns `id`/`debate_id` when a persona is saved, so those fields of the
    frozen Persona contract are intentionally absent here.
    """

    archetype: Archetype
    name: str = Field(..., description="Display name, e.g. 'The Advocate'")
    color: str = Field(..., description="Stable color key for per-persona UI coding (DEC-006)")
    stance: str = Field(
        ..., description="This persona's position, framed to the decision (DEC-001)"
    )
