"""Cost guardrails for a single debate (PRD §9, DEC-007).

Hard caps on personas, rounds, and token spend are enforced *before* every LLM
call. A debate carries one :class:`DebateGuardrails` instance that accumulates
token usage as turns complete; a check that would breach a cap raises
:class:`CapExceededError`, which the LLM client catches to produce a skipped
turn rather than an uncontrolled (and potentially expensive) call.
"""

from dataclasses import dataclass

from app.core.config import get_settings


class GuardrailError(Exception):
    """Base class for guardrail violations."""


class CapExceededError(GuardrailError):
    """Raised before a call when a hard cap would be exceeded."""


@dataclass
class DebateGuardrails:
    """Per-debate cost tracker enforcing the hard caps before each call.

    Rounds are 1-indexed (round 1, round 2, ...); persona indices are
    0-indexed. ``tokens_spent`` accumulates input + output tokens across every
    completed call in the debate.
    """

    max_personas: int
    max_rounds: int
    max_tokens_per_debate: int
    tokens_spent: int = 0

    def check(
        self,
        *,
        round_number: int,
        persona_index: int,
        estimated_tokens: int = 0,
    ) -> None:
        """Enforce the hard caps before a call; raise if any would be breached."""
        if persona_index >= self.max_personas:
            raise CapExceededError(
                f"persona cap exceeded: index {persona_index} >= max {self.max_personas}"
            )
        if round_number > self.max_rounds:
            raise CapExceededError(
                f"round cap exceeded: round {round_number} > max {self.max_rounds}"
            )
        projected = self.tokens_spent + estimated_tokens
        if projected > self.max_tokens_per_debate:
            raise CapExceededError(
                f"token cap exceeded: projected {projected} > max {self.max_tokens_per_debate}"
            )

    def record_usage(self, tokens_in: int, tokens_out: int) -> None:
        """Add a completed call's token usage to the running debate total."""
        self.tokens_spent += tokens_in + tokens_out


def guardrails_from_settings() -> DebateGuardrails:
    """Build guardrails for a fresh debate from application settings."""
    settings = get_settings()
    return DebateGuardrails(
        max_personas=settings.max_personas,
        max_rounds=settings.max_rounds,
        max_tokens_per_debate=settings.max_tokens_per_debate,
    )
