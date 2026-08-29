"""Fixed persona archetypes and per-decision stance framing (DEC-001, DEC-002).

Three recognizable archetypes — The Advocate, The Skeptic, The Pragmatist —
whose specific stances are framed per decision. The archetype set is fixed and
deterministic (no dynamic/generated personas in the MVP); only the per-decision
stance varies. The system prompts here drive each persona's turns in the
orchestrator (TICKET-4); the stance seeds and previews each persona's position.
"""

from dataclasses import dataclass

from app.schemas.persona import Archetype


@dataclass(frozen=True)
class ArchetypeSpec:
    """The fixed definition of one archetype; only `stance` is framed per decision."""

    archetype: Archetype
    name: str
    color: str
    system_prompt: str
    stance_template: str  # must contain a `{decision}` placeholder

    def frame_stance(self, decision: str) -> str:
        """Frame this archetype's stance for a specific decision (DEC-001)."""
        return self.stance_template.format(decision=decision)


ADVOCATE = ArchetypeSpec(
    archetype=Archetype.ADVOCATE,
    name="The Advocate",
    color="emerald",
    system_prompt=(
        "You are The Advocate in a structured decision debate. You argue for bold "
        "action and change: champion the opportunity, articulate the strongest upside, "
        "and push toward committing. Engage directly with the other personas' points "
        "from the transcript rather than repeating yourself. Be persuasive but honest — "
        "never invent facts."
    ),
    stance_template=(
        'Seize it — "{decision}" is worth committing to; the upside outweighs the risk.'
    ),
)

SKEPTIC = ArchetypeSpec(
    archetype=Archetype.SKEPTIC,
    name="The Skeptic",
    color="rose",
    system_prompt=(
        "You are The Skeptic in a structured decision debate. You challenge assumptions "
        "and surface risks, hidden costs, and unknowns; argue for caution, more evidence, "
        "or the status quo where the case is weak. Engage directly with the other personas' "
        "points from the transcript rather than repeating yourself. Be rigorous, not "
        "cynical — never invent facts."
    ),
    stance_template=(
        'Hold on — "{decision}" carries risks and unknowns that are not resolved yet.'
    ),
)

PRAGMATIST = ArchetypeSpec(
    archetype=Archetype.PRAGMATIST,
    name="The Pragmatist",
    color="indigo",
    system_prompt=(
        "You are The Pragmatist in a structured decision debate. You weigh trade-offs and "
        "seek the workable middle path grounded in the user's real constraints, turning the "
        "disagreement into concrete conditions and next steps. Engage directly with the "
        "other personas' points from the transcript rather than repeating yourself. Be "
        "balanced and specific — never invent facts."
    ),
    stance_template=(
        'It depends — the right call on "{decision}" hinges on your constraints and trade-offs.'
    ),
)


# Fixed set, deterministic order — exactly three personas (DEC-002).
ARCHETYPES: tuple[ArchetypeSpec, ...] = (ADVOCATE, SKEPTIC, PRAGMATIST)

_BY_ARCHETYPE: dict[Archetype, ArchetypeSpec] = {spec.archetype: spec for spec in ARCHETYPES}


def spec_for(archetype: Archetype) -> ArchetypeSpec:
    """Return the fixed spec for an archetype."""
    return _BY_ARCHETYPE[archetype]


def system_prompt_for(archetype: Archetype) -> str:
    """Return the system prompt that drives an archetype's turns (used by TICKET-4)."""
    return _BY_ARCHETYPE[archetype].system_prompt
