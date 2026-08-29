import logging
import time
from collections.abc import Callable
from typing import Literal

from anthropic import Anthropic, APIError
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.guardrails import CapExceededError, DebateGuardrails
from app.schemas.chat import ChatMessage, ChatResponse

logger = logging.getLogger(__name__)

Tier = Literal["personas", "judge", "utility"]


class TurnResult(BaseModel):
    """In-memory result of a single guarded LLM turn.

    Decoupled from persistence (TICKET-1): the orchestrator maps this onto a
    ``Turn`` row. ``status`` is ``"ok"`` when text was produced, or ``"skipped"``
    when a guardrail blocked the call or every retry failed.
    """

    status: Literal["ok", "skipped"]
    model: str
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    reason: str | None = None


class LLMService:
    """Thin, guarded wrapper around the Anthropic Messages API."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        settings = get_settings()
        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._models: dict[Tier, str] = {
            "personas": settings.model_personas,
            "judge": settings.model_judge,
            "utility": settings.model_utility,
        }
        self._max_retries = settings.llm_max_retries
        self._backoff_base = settings.llm_backoff_base_seconds
        # Injectable so tests never actually sleep between retries.
        self._sleep = sleep

    def model_for(self, tier: Tier) -> str:
        """Resolve the model id for a tier (DEC-007 model routing)."""
        return self._models[tier]

    def chat(self, messages: list[ChatMessage], max_tokens: int = 1024) -> ChatResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResponse(content=text, model=self._model)

    def run_turn(
        self,
        *,
        tier: Tier,
        messages: list[ChatMessage],
        guardrails: DebateGuardrails,
        round_number: int,
        persona_index: int,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> TurnResult:
        """Run one guarded, retrying LLM turn.

        Enforces guardrails *before* the call, retries transient API failures
        with exponential backoff, logs cost/latency per attempt, and returns a
        ``skipped`` :class:`TurnResult` (never raises) when blocked or when every
        attempt fails.
        """
        model = self.model_for(tier)

        # (a) Guardrails BEFORE the call — a breached cap blocks it outright.
        try:
            guardrails.check(
                round_number=round_number,
                persona_index=persona_index,
                estimated_tokens=max_tokens,
            )
        except CapExceededError as exc:
            logger.warning(
                "turn blocked by guardrail: tier=%s round=%s persona=%s reason=%s",
                tier,
                round_number,
                persona_index,
                exc,
            )
            return TurnResult(status="skipped", model=model, reason=f"guardrail: {exc}")

        payload = [{"role": m.role, "content": m.content} for m in messages]

        # (b) Retry transient failures with backoff.
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            start = time.perf_counter()
            try:
                response = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=payload,
                    **({"system": system} if system is not None else {}),
                )
            except APIError as exc:
                last_error = exc
                logger.warning(
                    "turn attempt failed: tier=%s round=%s persona=%s attempt=%s/%s error=%s",
                    tier,
                    round_number,
                    persona_index,
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
                if attempt < self._max_retries:
                    self._sleep(self._backoff_base * (2**attempt))
                continue

            latency_ms = (time.perf_counter() - start) * 1000
            text = "".join(block.text for block in response.content if block.type == "text")
            usage = getattr(response, "usage", None)
            tokens_in = getattr(usage, "input_tokens", 0) or 0
            tokens_out = getattr(usage, "output_tokens", 0) or 0

            # (d) Cost/latency logged per call.
            logger.info(
                "turn ok: tier=%s model=%s round=%s persona=%s "
                "tokens_in=%s tokens_out=%s latency_ms=%.1f",
                tier,
                model,
                round_number,
                persona_index,
                tokens_in,
                tokens_out,
                latency_ms,
            )
            guardrails.record_usage(tokens_in, tokens_out)
            return TurnResult(
                status="ok",
                model=model,
                text=text,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
            )

        # (c) Every attempt failed — degrade to a skipped turn instead of raising.
        logger.error(
            "turn skipped after %s attempts: tier=%s round=%s persona=%s error=%s",
            self._max_retries + 1,
            tier,
            round_number,
            persona_index,
            last_error,
        )
        return TurnResult(
            status="skipped",
            model=model,
            reason=f"llm error after {self._max_retries + 1} attempts: {last_error}",
        )


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Lazy singleton so the client is only built when first needed."""
    global _service
    if _service is None:
        _service = LLMService()
    return _service
