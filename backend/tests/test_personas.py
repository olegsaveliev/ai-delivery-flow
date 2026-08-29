import pytest

from app.prompts.personas import ARCHETYPES, system_prompt_for
from app.schemas.persona import Archetype, Persona
from app.services.personas import assign_personas

SAMPLE_DECISION = "Should I take the senior role at a startup or stay at my stable job?"


def test_returns_exactly_three_personas():
    """DEC-002 — exactly three personas."""
    personas = assign_personas(SAMPLE_DECISION)
    assert len(personas) == 3
    assert all(isinstance(p, Persona) for p in personas)


def test_fixed_archetype_set_in_order():
    """DEC-001 — the fixed, recognizable archetype set (deterministic order)."""
    personas = assign_personas(SAMPLE_DECISION)
    assert [p.archetype for p in personas] == [
        Archetype.ADVOCATE,
        Archetype.SKEPTIC,
        Archetype.PRAGMATIST,
    ]
    assert [p.name for p in personas] == ["The Advocate", "The Skeptic", "The Pragmatist"]


def test_archetypes_and_colors_are_distinct():
    """Each persona is visually and semantically distinct (DEC-006 color coding)."""
    personas = assign_personas(SAMPLE_DECISION)
    assert len({p.archetype for p in personas}) == 3
    assert len({p.color for p in personas}) == 3
    assert all(p.color for p in personas)


def test_stance_is_framed_per_decision():
    """DEC-001 — stances are framed to the specific decision."""
    personas = assign_personas(SAMPLE_DECISION)
    assert all(SAMPLE_DECISION in p.stance for p in personas)
    # And each persona argues a different position.
    assert len({p.stance for p in personas}) == 3


def test_different_decisions_produce_different_stances():
    a = assign_personas("React or Svelte?")
    b = assign_personas("Buy or rent a home?")
    assert [p.stance for p in a] != [p.stance for p in b]
    # ...but the archetype set is unchanged.
    assert [p.archetype for p in a] == [p.archetype for p in b]


def test_deterministic_given_same_input():
    first = assign_personas(SAMPLE_DECISION)
    second = assign_personas(SAMPLE_DECISION)
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]


def test_context_does_not_change_the_archetype_set():
    without = assign_personas(SAMPLE_DECISION)
    with_context = assign_personas(SAMPLE_DECISION, context="I value autonomy and growth.")
    assert [p.archetype for p in without] == [p.archetype for p in with_context]


def test_decision_is_stripped():
    personas = assign_personas(f"   {SAMPLE_DECISION}   ")
    assert all(SAMPLE_DECISION in p.stance for p in personas)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_empty_decision_raises(bad):
    with pytest.raises(ValueError, match="non-empty"):
        assign_personas(bad)


def test_system_prompts_are_distinct_and_nonempty():
    """Each archetype drives a distinct persona voice (used by the orchestrator, TICKET-4)."""
    prompts = [system_prompt_for(spec.archetype) for spec in ARCHETYPES]
    assert all(prompts)
    assert len(set(prompts)) == 3
