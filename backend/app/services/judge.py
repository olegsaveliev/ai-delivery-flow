"""Judge synthesis with structured output + repair retry (TICKET-5 / KAN-8, DEC-007).

Runs last on the full debate transcript. ``judge(debate)`` renders the transcript into
an Opus-tier prompt (DEC-007), asks for a JSON verdict, then parses and validates it
against :class:`app.schemas.verdict.Verdict`. On a parse/validation failure it performs
**exactly one** repair retry (re-prompting with the bad output + error); a second
failure raises :class:`JudgeSchemaError`.

``judge`` is pure — no HTTP, no session, no persistence — so the API layer (TICKET-6)
can compose ``run_debate -> judge -> persist_verdict``. ``persist_verdict`` maps the
validated verdict onto the existing repository upsert.
"""

import json
import logging

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.debate import Debate
from app.models.enums import TurnStatus
from app.models.verdict import Verdict as VerdictRow
from app.prompts.judge import JUDGE_SYSTEM_PROMPT, build_judge_messages, build_repair_message
from app.repositories import debates as repo
from app.schemas.chat import ChatMessage
from app.schemas.verdict import Verdict
from app.services.llm import LLMService, get_llm_service

logger = logging.getLogger(__name__)


class JudgeError(Exception):
    """Base class for judge failures."""


class JudgeSchemaError(JudgeError):
    """Raised when the verdict is still schema-invalid after one repair retry."""


def _render_transcript(debate: Debate) -> list[str]:
    """Render OK turns as ``[Round N] {name}: {content}`` (skips SKIPPED/empty turns)."""
    names = {p.id: p.name for p in debate.personas}
    return [
        f"[Round {t.round}] {names.get(t.persona_id, '?')}: {t.content}"
        for t in debate.turns
        if t.status is TurnStatus.OK and t.content
    ]


def _parse_and_validate(text: str) -> Verdict:
    """Parse a JSON verdict from model text and validate it.

    Tolerates an optional ```json ...``` fence. Raises ``json.JSONDecodeError`` for
    non-JSON or ``pydantic.ValidationError`` for JSON that fails the schema — both are
    handled identically by the repair caller.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Strip an opening fence (``` optionally followed by a language tag such as
        # ``json``) and any trailing fence — works for both single- and multi-line.
        stripped = stripped[3:]
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    data = json.loads(stripped)
    return Verdict.model_validate(data)


def judge(debate: Debate, *, llm: LLMService | None = None, max_tokens: int = 2048) -> Verdict:
    """Synthesize a schema-validated verdict from the debate transcript.

    Uses the Opus tier (DEC-007). Performs exactly one repair retry on a parse/schema
    failure, then raises :class:`JudgeSchemaError`.
    """
    llm = llm or get_llm_service()
    messages = build_judge_messages(
        decision=debate.decision,
        context=debate.context,
        transcript_lines=_render_transcript(debate),
    )

    first = llm.complete(
        tier="judge", messages=messages, system=JUDGE_SYSTEM_PROMPT, max_tokens=max_tokens
    )
    try:
        return _parse_and_validate(first)
    except (json.JSONDecodeError, ValidationError) as err:
        # Capture the error text: the `except ... as` name is unbound after the block.
        first_error = str(err)
        logger.warning(
            "judge verdict invalid, repairing: debate=%s error=%s", debate.id, first_error
        )

    # Exactly one repair retry: user(prompt) -> assistant(first) -> user(repair).
    repair_messages = [
        *messages,
        ChatMessage(role="assistant", content=first),
        build_repair_message(bad_output=first, error=first_error),
    ]
    second = llm.complete(
        tier="judge", messages=repair_messages, system=JUDGE_SYSTEM_PROMPT, max_tokens=max_tokens
    )
    try:
        return _parse_and_validate(second)
    except (json.JSONDecodeError, ValidationError) as err:
        raise JudgeSchemaError(
            f"verdict invalid after one repair (debate={debate.id}): {err}"
        ) from err


def persist_verdict(session: Session, debate: Debate, verdict: Verdict) -> VerdictRow:
    """Persist a validated verdict for a debate (upserts the unique row — TICKET-1)."""
    return repo.set_verdict(
        session,
        debate.id,
        recommendation=verdict.recommendation,
        cases=[c.model_dump() for c in verdict.cases],
        tradeoffs=verdict.tradeoffs,
    )
