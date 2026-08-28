"""Explicit-session CRUD for the debate aggregate.

Commit is the caller's responsibility (use ``get_session()``); these functions
``flush`` to populate server/default values (ids, timestamps) and return live
ORM objects. No HTTP concerns live here.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.debate import Debate
from app.models.enums import Archetype, TurnStatus
from app.models.persona import Persona
from app.models.turn import Turn
from app.models.verdict import Verdict


def create_debate(session: Session, decision: str, context: str | None = None) -> Debate:
    debate = Debate(decision=decision, context=context)
    session.add(debate)
    session.flush()
    return debate


def add_persona(
    session: Session,
    debate_id: str,
    archetype: Archetype,
    name: str,
    stance: str,
) -> Persona:
    persona = Persona(
        debate_id=debate_id,
        archetype=archetype,
        name=name,
        stance=stance,
    )
    session.add(persona)
    session.flush()
    return persona


def add_turn(
    session: Session,
    debate_id: str,
    persona_id: str,
    round: int,
    content: str,
    status: TurnStatus = TurnStatus.OK,
) -> Turn:
    turn = Turn(
        debate_id=debate_id,
        persona_id=persona_id,
        round=round,
        content=content,
        status=status,
    )
    session.add(turn)
    session.flush()
    return turn


def set_verdict(
    session: Session,
    debate_id: str,
    recommendation: str,
    cases: list,
    tradeoffs: list,
) -> Verdict:
    """Upsert the single verdict for a debate (respects the ``unique`` FK)."""
    existing = session.scalars(select(Verdict).where(Verdict.debate_id == debate_id)).one_or_none()
    if existing is not None:
        session.delete(existing)
        session.flush()

    verdict = Verdict(
        debate_id=debate_id,
        recommendation=recommendation,
        cases=cases,
        tradeoffs=tradeoffs,
    )
    session.add(verdict)
    session.flush()
    return verdict


def get_debate(session: Session, debate_id: str) -> Debate | None:
    """Load a debate with personas, turns, and verdict eager-loaded.

    Turns are ordered by ``round`` then ``created_at`` (transcript order).
    """
    debate = session.scalars(
        select(Debate)
        .where(Debate.id == debate_id)
        .options(
            selectinload(Debate.personas),
            selectinload(Debate.turns),
            selectinload(Debate.verdict),
        )
    ).one_or_none()
    if debate is None:
        return None

    # round is the primary transcript order; created_at then id break ties
    # deterministically for turns persisted concurrently within a round (T4).
    debate.turns.sort(key=lambda t: (t.round, t.created_at, t.id))
    return debate


def list_debates(session: Session) -> list[Debate]:
    """All debates, newest first. No ownership filter (DEC-005).

    ``created_at`` (naive utcnow) frequently ties between rows created in the
    same microsecond, so ``id`` is a deterministic tiebreaker — the order is
    stable across calls. The uuid hex isn't time-ordered, so ties resolve
    arbitrarily-but-consistently; under DEC-005 (single-user, debates created
    minutes apart) genuine same-instant ties don't occur in practice.
    """
    return list(
        session.scalars(
            select(Debate)
            .order_by(Debate.created_at.desc(), Debate.id.desc())
            .options(
                selectinload(Debate.personas),
                selectinload(Debate.turns),
                selectinload(Debate.verdict),
            )
        )
    )


def delete_debate(session: Session, debate_id: str) -> bool:
    """Delete a debate and cascade to its children. False if it doesn't exist."""
    debate = session.get(Debate, debate_id)
    if debate is None:
        return False
    session.delete(debate)
    session.flush()
    return True
