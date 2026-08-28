from sqlalchemy import func, select

from app.models.enums import Archetype, DebateStatus, TurnStatus
from app.models.persona import Persona
from app.models.turn import Turn
from app.models.verdict import Verdict
from app.repositories import debates as repo


def _build_full_debate(session):
    """create → 3 personas → 6 turns (2 rounds) → verdict. Returns the debate."""
    debate = repo.create_debate(session, decision="Adopt SQLite?", context="dev tooling")

    advocate = repo.add_persona(session, debate.id, Archetype.ADVOCATE, "Ada", "Yes, ship it")
    skeptic = repo.add_persona(session, debate.id, Archetype.SKEPTIC, "Skeeter", "Too risky")
    pragmatist = repo.add_persona(
        session, debate.id, Archetype.PRAGMATIST, "Prue", "Depends on scale"
    )

    for round_no in (1, 2):
        for persona in (advocate, skeptic, pragmatist):
            repo.add_turn(
                session,
                debate.id,
                persona.id,
                round=round_no,
                content=f"{persona.name} round {round_no}",
            )

    repo.set_verdict(
        session,
        debate.id,
        recommendation="Use SQLite for dev",
        cases=[{"option": "SQLite", "argument": "simple"}],
        tradeoffs=["not for scale"],
    )
    return debate


def test_create_and_get_debate_roundtrip(session):
    debate = _build_full_debate(session)

    got = repo.get_debate(session, debate.id)
    assert got is not None
    assert got.decision == "Adopt SQLite?"
    assert got.context == "dev tooling"
    assert got.status == DebateStatus.PENDING
    assert len(got.personas) == 3
    assert {p.archetype for p in got.personas} == {
        Archetype.ADVOCATE,
        Archetype.SKEPTIC,
        Archetype.PRAGMATIST,
    }

    # Turns returned in transcript order (round asc, then created_at).
    assert len(got.turns) == 6
    assert [t.round for t in got.turns] == [1, 1, 1, 2, 2, 2]

    assert got.verdict is not None
    assert got.verdict.recommendation == "Use SQLite for dev"
    assert got.verdict.cases == [{"option": "SQLite", "argument": "simple"}]
    assert got.verdict.tradeoffs == ["not for scale"]


def test_get_missing_returns_none(session):
    assert repo.get_debate(session, "does-not-exist") is None


def test_list_debates_newest_first(session):
    first = repo.create_debate(session, decision="First")
    second = repo.create_debate(session, decision="Second")
    # Nudge created_at so ordering is deterministic regardless of clock resolution.
    second.created_at = first.created_at.replace(microsecond=0)
    first.created_at = first.created_at.replace(year=first.created_at.year - 1)
    session.flush()

    listed = repo.list_debates(session)
    assert len(listed) == 2
    assert listed[0].id == second.id
    assert listed[1].id == first.id


def test_list_debates_empty(session):
    assert repo.list_debates(session) == []


def test_delete_debate_cascades(session):
    debate = _build_full_debate(session)

    assert repo.delete_debate(session, debate.id) is True
    assert repo.get_debate(session, debate.id) is None

    # Verify at the table level that children are gone, not just via ORM.
    assert session.scalar(select(func.count()).select_from(Persona)) == 0
    assert session.scalar(select(func.count()).select_from(Turn)) == 0
    assert session.scalar(select(func.count()).select_from(Verdict)) == 0


def test_delete_missing_returns_false(session):
    assert repo.delete_debate(session, "does-not-exist") is False


def test_set_verdict_replaces_existing(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")

    repo.set_verdict(session, debate.id, recommendation="First", cases=[], tradeoffs=[])
    repo.set_verdict(session, debate.id, recommendation="Second", cases=[], tradeoffs=["b"])

    verdicts = list(session.scalars(select(Verdict).where(Verdict.debate_id == debate.id)))
    assert len(verdicts) == 1
    assert verdicts[0].recommendation == "Second"


def test_skipped_turn_persists(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    persona = repo.add_persona(session, debate.id, Archetype.ADVOCATE, "Ada", "Yes")
    repo.add_turn(
        session,
        debate.id,
        persona.id,
        round=1,
        content="",
        status=TurnStatus.SKIPPED,
    )

    got = repo.get_debate(session, debate.id)
    assert len(got.turns) == 1
    assert got.turns[0].status == TurnStatus.SKIPPED
