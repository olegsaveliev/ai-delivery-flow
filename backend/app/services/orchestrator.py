"""Debate orchestrator — concurrent turns, sequential rounds (TICKET-4 / KAN-7).

The engine at the heart of Second Opinion. Given a persisted :class:`Debate`, it
assigns the three fixed personas (DEC-001, DEC-002), then runs a fixed **2 rounds**
(DEC-003) in which the three persona turns within a round run **concurrently** while
rounds run **sequentially** — round N receives the transcript of every round ``< N``
so personas answer each other. Each completed turn is persisted to SQLite as it
lands (DEC-008); a failed or guardrail-blocked turn is recorded as ``skipped`` and
the debate continues. Personas run on the Sonnet tier (DEC-007).

Concurrency model: KAN-6 shipped a *synchronous* ``LLMService`` (blocking client +
``time.sleep`` backoff). The three per-round turns are fanned out across a thread
pool via :func:`asyncio.to_thread`; ``run_debate`` itself is a coroutine. All DB
writes stay on the orchestrator's own thread because a SQLAlchemy ``Session`` is not
thread-safe — only :meth:`LLMService.run_turn` runs in worker threads.

No HTTP concerns live here (that is TICKET-6). ``run_debate`` returns the ``Debate``
whose ``turns`` are the transcript the judge (TICKET-5) consumes.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.guardrails import DebateGuardrails, guardrails_from_settings
from app.models.debate import Debate
from app.models.enums import Archetype as ModelArchetype
from app.models.enums import DebateStatus, TurnStatus
from app.prompts.personas import system_prompt_for
from app.repositories import debates as repo
from app.schemas.chat import ChatMessage
from app.services.llm import LLMService, get_llm_service
from app.services.personas import assign_personas

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TurnRecord:
    """One OK turn accumulated in-memory to build later rounds' transcripts."""

    name: str
    archetype: str
    round: int
    content: str


def _build_turn_messages(
    *,
    decision: str,
    context: str | None,
    stance: str,
    transcript: list[_TurnRecord],
    round_number: int,
) -> list[ChatMessage]:
    """Assemble the single user message that drives one persona's turn.

    The persona's *system* prompt is passed separately to ``run_turn``; this builds
    the user turn: the decision (+ optional context), this persona's stance, the
    prior-rounds transcript (rounds ``< round_number``), and a closing instruction.
    """
    lines: list[str] = [f'Decision under debate: "{decision}"']
    if context and context.strip():
        lines.append(f"Context: {context.strip()}")
    lines.append(f"Your stance: {stance}")

    lines.append("")
    if transcript:
        lines.append("Debate so far:")
        for rec in transcript:
            lines.append(f"[Round {rec.round}] {rec.name}: {rec.content}")
    else:
        lines.append("No prior discussion yet — this is the opening round.")

    lines.append("")
    lines.append(
        f"Give your argument for round {round_number}. Engage directly with the other "
        "personas' points above rather than repeating yourself; stay true to your stance."
    )
    return [ChatMessage(role="user", content="\n".join(lines))]


async def run_debate(
    session: Session,
    debate: Debate,
    *,
    llm: LLMService | None = None,
    guardrails: DebateGuardrails | None = None,
    max_tokens_per_turn: int = 1024,
) -> Debate:
    """Run a full debate: assign personas, then N sequential rounds of concurrent turns.

    Returns the debate reloaded with personas, turns (in transcript order), and any
    verdict. Turns that fail or are guardrail-blocked are persisted as ``SKIPPED``
    (empty content) and never abort the debate.
    """
    llm = llm or get_llm_service()
    guardrails = guardrails or guardrails_from_settings()
    max_rounds = get_settings().max_rounds  # DEC-003: fixed rounds, no convergence.

    # Assign (DEC-001/002) and persist the three personas. ``assign_personas`` returns
    # schema Personas (schemas.persona.Archetype); the repo takes the models enum.
    assigned = assign_personas(debate.decision, debate.context)
    persona_pairs = []  # (orm_persona, schema_persona)
    for p in assigned:
        row = repo.add_persona(
            session, debate.id, ModelArchetype(p.archetype.value), p.name, p.stance
        )
        persona_pairs.append((row, p))
    session.commit()

    debate.status = DebateStatus.RUNNING
    session.commit()
    logger.info(
        "debate started: id=%s rounds=%s personas=%s", debate.id, max_rounds, len(persona_pairs)
    )

    transcript: list[_TurnRecord] = []

    for round_number in range(1, max_rounds + 1):
        prior = list(transcript)  # rounds < round_number

        async def _one(index: int, row, schema_p, _round=round_number, _prior=prior):
            messages = _build_turn_messages(
                decision=debate.decision,
                context=debate.context,
                stance=schema_p.stance,
                transcript=_prior,
                round_number=_round,
            )
            # Synchronous run_turn fanned out to a worker thread; it never raises.
            result = await asyncio.to_thread(
                llm.run_turn,
                tier="personas",
                messages=messages,
                guardrails=guardrails,
                round_number=_round,
                persona_index=index,
                max_tokens=max_tokens_per_turn,
                system=system_prompt_for(schema_p.archetype),
            )
            return index, row, result

        tasks = [
            asyncio.create_task(_one(index, row, schema_p))
            for index, (row, schema_p) in enumerate(persona_pairs)
        ]

        round_results = []
        # Persist each turn as it lands — writes stay on this thread (Session is not
        # thread-safe); a later failure can never lose an earlier completed turn.
        for finished in asyncio.as_completed(tasks):
            index, row, result = await finished
            status = TurnStatus.OK if result.status == "ok" else TurnStatus.SKIPPED
            repo.add_turn(
                session,
                debate.id,
                row.id,
                round=round_number,
                content=result.text or "",  # content is NOT nullable; skips persist ""
                status=status,
            )
            session.commit()
            if status is TurnStatus.SKIPPED:
                logger.warning(
                    "turn skipped: debate=%s round=%s persona=%s reason=%s",
                    debate.id,
                    round_number,
                    index,
                    result.reason,
                )
            round_results.append((index, row, result))

        # Accumulate OK turns in persona order so the next round reads a deterministic
        # transcript (persisted order is completion order, re-sorted by get_debate).
        for index, row, result in sorted(round_results, key=lambda x: x[0]):
            if result.status == "ok":
                transcript.append(
                    _TurnRecord(row.name, row.archetype.value, round_number, result.text)
                )

    debate.status = DebateStatus.COMPLETED
    session.commit()
    logger.info("debate completed: id=%s turns=%s", debate.id, len(transcript))

    # Reload with children eager-loaded and turns in transcript order for the judge.
    reloaded = repo.get_debate(session, debate.id)
    return reloaded if reloaded is not None else debate
