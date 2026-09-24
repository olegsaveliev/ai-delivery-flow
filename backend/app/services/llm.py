import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from anthropic import (
    Anthropic,
    APIConnectionError,
    APIError,
    APIStatusError,
    Timeout,
)
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


class _RetryableStreamError(Exception):
    """Internal signal: a stream attempt failed transiently before any delta (safe to retry)."""


@dataclass(frozen=True)
class _TurnCtx:
    """Identity of one streamed turn, threaded through the failure/timeout helpers."""

    tier: Tier
    model: str
    round_number: int
    persona_index: int


# HTTP statuses worth retrying (mirrors the SDK's own policy) and the error types a
# mid-stream `error` SSE event can carry that are transient. Mid-stream error events
# arrive as APIStatusError with the *200* status of the already-open response, so the
# body's error type is the only signal there.
_RETRYABLE_STATUS = frozenset({408, 409, 429})
_RETRYABLE_STREAM_ERROR_TYPES = frozenset(
    {"overloaded_error", "api_error", "rate_limit_error", "timeout_error"}
)
# Connect timeout for a streamed attempt; never more than the remaining budget.
_STREAM_CONNECT_TIMEOUT_S = 5.0


def _is_transient_error(exc: BaseException) -> bool:
    """True for failures worth retrying before the first delta (network / server-side).

    Honors the server's ``x-should-retry`` header when present, as the SDK's own
    retry loop does. ``retry-after`` is not honored: backoff stays the fixed
    exponential schedule, capped by the remaining turn budget.
    """
    if isinstance(exc, APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, APIStatusError):
        headers = getattr(exc.response, "headers", None)
        should_retry = headers.get("x-should-retry") if headers is not None else None
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
        status = exc.status_code
        if status in _RETRYABLE_STATUS or status >= 500:
            return True
        body = exc.body
        error = body.get("error") if isinstance(body, dict) else None
        return isinstance(error, dict) and error.get("type") in _RETRYABLE_STREAM_ERROR_TYPES
    if isinstance(exc, APIError):
        return False
    # The SDK leaks raw transport errors (httpx2, its hard dependency) unwrapped from
    # mid-stream body reads.
    return isinstance(exc, (httpx2.TransportError, OSError))


def _partial_usage(stream: Any) -> tuple[int, int]:
    """Best-effort (tokens_in, tokens_out) billed so far on an aborted stream. Never raises.

    ``current_message_snapshot`` asserts until ``message_start`` has arrived, so any
    failure here simply means nothing is known to have been billed yet. Output tokens
    are only updated by ``message_delta`` (sent at the end), so a stream cut off
    mid-text under-reports them.
    """
    if stream is None:
        return 0, 0
    try:
        usage = stream.current_message_snapshot.usage
        tokens_in = getattr(usage, "input_tokens", 0) or 0
        tokens_out = getattr(usage, "output_tokens", 0) or 0
    except Exception:  # noqa: BLE001 — best-effort read; must never raise
        return 0, 0
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return 0, 0
    return tokens_in, tokens_out


class LLMService:
    """Thin, guarded wrapper around the Anthropic Messages API."""

    def __init__(
        self,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
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
        self._turn_timeout = settings.turn_timeout_seconds
        # Injectable monotonic clock so tests can drive the per-turn deadline deterministically.
        self._clock = clock
        # Streaming client with SDK retries off (shares the HTTP pool): stream_turn's own
        # loop is its only retry layer, so the per-turn budget isn't multiplied.
        self._stream_client = self._client.with_options(max_retries=0)

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

    def stream_turn(
        self,
        *,
        on_delta: Callable[[str], None],
        messages: list[ChatMessage],
        guardrails: DebateGuardrails,
        round_number: int,
        persona_index: int,
        tier: Tier = "personas",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> TurnResult:
        """Run one guarded LLM turn on the streaming Messages API (KAN-18).

        Keeps :meth:`run_turn`'s guarantees: guardrails are checked *before* the
        call (a breach returns ``skipped`` with no API call and no deltas),
        cost/latency is logged, usage is recorded to ``guardrails``, and it
        **never raises**.

        Threading contract: this is synchronous and blocking, meant to run in a
        worker thread (``asyncio.to_thread``). ``on_delta`` is a plain sync
        callable invoked on the calling (worker) thread, once per non-empty text
        delta, in generation order; it must be fast, must not block, and must not
        assume it runs on the event loop (the orchestrator bridges it with
        ``loop.call_soon_threadsafe``). Concurrency: same properties as
        ``run_turn`` — concurrent calls may share one ``guardrails`` object, but
        its check-then-record steps are not atomic, so a round of concurrent turns
        can overshoot the token cap slightly (pre-existing; a lock is a follow-up).

        Result: on ``ok``, ``text == "".join(every delta passed to on_delta)``. On
        ``skipped``, ``text == ""`` even if some deltas were already emitted —
        consumers must discard partial text for that turn. Usage billed by aborted
        or retried attempts is charged best-effort from the stream snapshot: input
        tokens are exact once ``message_start`` has arrived, but output tokens may
        be under-counted because the API only reports them at the end.

        Failure policy:

        * A *transient* failure before the first delta (connection error or
          connect timeout with budget left, 408/409/429/5xx, overloaded/api/rate
          limit error event, raw transport error; ``x-should-retry`` honored) is
          retried with exponential backoff. This loop is the only retry layer: the
          SDK's own retries are disabled for the streaming call, and
          ``retry-after`` is not honored.
        * A non-transient failure before the first delta (other 4xx, bugs such as
          ``TypeError``) is skipped at once, without retrying.
        * A failure *after* a delta was emitted (including ``on_delta`` raising) is
          not retried; the turn is ``skipped`` so no client sees duplicated text.
        * ``turn_timeout_seconds`` is one wall-clock budget spanning every attempt
          and backoff. A failure counts as ``reason == "timeout"`` when the budget
          is spent (and it is not an API status error), and is never retried.

        Bound: the budget is enforced per attempt through the request timeout
        (read/write/pool = remaining budget, connect = min(5 s, remaining)) and at
        every text delta. A stall is therefore cut off after at most the remaining
        budget, but a stream that sends only keepalive pings and no text keeps
        resetting the read timeout and can exceed the budget; it is bounded only by
        the per-debate timeout (EPIC-B B-T5).
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
                "stream turn blocked by guardrail: tier=%s round=%s persona=%s reason=%s",
                tier,
                round_number,
                persona_index,
                exc,
            )
            return TurnResult(status="skipped", model=model, reason=f"guardrail: {exc}")

        ctx = _TurnCtx(
            tier=tier, model=model, round_number=round_number, persona_index=persona_index
        )
        payload = [{"role": m.role, "content": m.content} for m in messages]
        # One wall-clock budget for the whole turn, across retries and backoff.
        deadline = self._clock() + self._turn_timeout

        # (b) Retry transient failures that happen before any delta was emitted.
        last_error: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            if self._clock() >= deadline:
                return self._timeout_result(ctx, guardrails, deltas=0, error=last_error)
            try:
                return self._stream_attempt(
                    ctx,
                    payload=payload,
                    max_tokens=max_tokens,
                    system=system,
                    on_delta=on_delta,
                    guardrails=guardrails,
                    deadline=deadline,
                )
            except _RetryableStreamError as exc:
                last_error = exc.__cause__ or exc
                logger.warning(
                    "stream turn attempt failed before first delta: tier=%s round=%s "
                    "persona=%s attempt=%s/%s error=%r",
                    tier,
                    round_number,
                    persona_index,
                    attempt + 1,
                    self._max_retries + 1,
                    last_error,
                )
                if attempt < self._max_retries:
                    # Never sleep past the deadline; the loop-top check then times out.
                    remaining = max(0.0, deadline - self._clock())
                    self._sleep(min(self._backoff_base * (2**attempt), remaining))

        # (c) Every attempt failed — degrade to a skipped turn instead of raising.
        logger.error(
            "stream turn skipped after %s attempts: tier=%s round=%s persona=%s error=%s",
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

    def _stream_attempt(
        self,
        ctx: _TurnCtx,
        *,
        payload: list[dict[str, str]],
        max_tokens: int,
        system: str | None,
        on_delta: Callable[[str], None],
        guardrails: DebateGuardrails,
        deadline: float,
    ) -> TurnResult:
        """Run a single streaming attempt.

        Returns a terminal :class:`TurnResult` (ok, timeout, or skip), or raises
        :class:`_RetryableStreamError` when it failed transiently before any delta.
        """
        remaining = max(0.0, deadline - self._clock())
        chunks: list[str] = []
        emitted = False
        deadline_hit = False
        stream: Any = None
        start = time.perf_counter()
        try:
            with self._stream_client.messages.stream(
                model=ctx.model,
                max_tokens=max_tokens,
                messages=payload,
                timeout=Timeout(remaining, connect=min(_STREAM_CONNECT_TIMEOUT_S, remaining)),
                **({"system": system} if system is not None else {}),
            ) as stream:
                for delta in stream.text_stream:
                    if not delta:
                        continue  # never forward empty chunks (KAN-17 contract)
                    # Check BEFORE emitting so no delta goes out past the deadline;
                    # breaking out exits the `with`, which closes the stream.
                    if self._clock() >= deadline:
                        deadline_hit = True
                        break
                    # Set before the callback: if on_delta raises, text may be out.
                    emitted = True
                    on_delta(delta)
                    chunks.append(delta)
                final_message = None if deadline_hit else stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 — classified below; never-raises contract
            return self._classify_failure(
                exc,
                ctx,
                stream=stream,
                emitted=emitted,
                deltas=len(chunks),
                guardrails=guardrails,
                deadline=deadline,
            )

        if deadline_hit:
            return self._timeout_result(
                ctx, guardrails, deltas=len(chunks), usage=_partial_usage(stream)
            )

        latency_ms = (time.perf_counter() - start) * 1000
        usage = getattr(final_message, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) or 0
        tokens_out = getattr(usage, "output_tokens", 0) or 0

        # (d) Cost/latency logged per call, same fields as run_turn.
        logger.info(
            "stream turn ok: tier=%s model=%s round=%s persona=%s "
            "tokens_in=%s tokens_out=%s latency_ms=%.1f",
            ctx.tier,
            ctx.model,
            ctx.round_number,
            ctx.persona_index,
            tokens_in,
            tokens_out,
            latency_ms,
        )
        guardrails.record_usage(tokens_in, tokens_out)
        return TurnResult(
            status="ok",
            model=ctx.model,
            text="".join(chunks),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )

    def _classify_failure(
        self,
        exc: Exception,
        ctx: _TurnCtx,
        *,
        stream: Any,
        emitted: bool,
        deltas: int,
        guardrails: DebateGuardrails,
        deadline: float,
    ) -> TurnResult:
        """Map a failed attempt to a terminal result, or raise ``_RetryableStreamError``.

        Timeout is decided by the budget, not the exception type: a failure is a
        timeout once the deadline has passed, unless it is an API status error (a
        5xx/429/overloaded error keeps its real reason). A connect timeout with
        budget left is therefore transient and retried.
        """
        usage = _partial_usage(stream)
        if self._clock() >= deadline and not isinstance(exc, APIStatusError):
            return self._timeout_result(ctx, guardrails, deltas=deltas, usage=usage, error=exc)
        if emitted:
            reason = f"stream interrupted after {deltas} deltas: {exc}"
            logger.warning(
                "stream turn interrupted after first delta (not retried): tier=%s round=%s "
                "persona=%s deltas=%s error=%r",
                ctx.tier,
                ctx.round_number,
                ctx.persona_index,
                deltas,
                exc,
            )
        elif _is_transient_error(exc):
            # Input already billed by this attempt still counts against the cap.
            self._charge(guardrails, usage)
            raise _RetryableStreamError(str(exc)) from exc
        else:
            reason = f"non-retryable error before first delta: {type(exc).__name__}: {exc}"
            logger.error(
                "stream turn failed with non-retryable error (not retried): tier=%s "
                "round=%s persona=%s error=%r",
                ctx.tier,
                ctx.round_number,
                ctx.persona_index,
                exc,
            )
        return self._skipped(ctx, guardrails, usage, reason)

    def _timeout_result(
        self,
        ctx: _TurnCtx,
        guardrails: DebateGuardrails,
        *,
        deltas: int,
        usage: tuple[int, int] = (0, 0),
        error: BaseException | None = None,
    ) -> TurnResult:
        """Log and build the ``skipped`` result for an exceeded per-turn budget."""
        logger.warning(
            "stream turn timed out: tier=%s round=%s persona=%s deltas=%s timeout_s=%s "
            "tokens_in=%s tokens_out=%s error=%r",
            ctx.tier,
            ctx.round_number,
            ctx.persona_index,
            deltas,
            self._turn_timeout,
            usage[0],
            usage[1],
            error,
        )
        return self._skipped(ctx, guardrails, usage, "timeout")

    @staticmethod
    def _charge(guardrails: DebateGuardrails, usage: tuple[int, int]) -> None:
        if usage[0] or usage[1]:
            guardrails.record_usage(*usage)

    def _skipped(
        self,
        ctx: _TurnCtx,
        guardrails: DebateGuardrails,
        usage: tuple[int, int],
        reason: str,
    ) -> TurnResult:
        """Skipped result that still charges tokens already billed to the debate cap."""
        self._charge(guardrails, usage)
        return TurnResult(
            status="skipped",
            model=ctx.model,
            tokens_in=usage[0],
            tokens_out=usage[1],
            reason=reason,
        )

    def complete(
        self,
        *,
        tier: Tier,
        messages: list[ChatMessage],
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Run a single guarded completion and return the raw text.

        Unlike :meth:`run_turn`, this carries no debate guardrails or persona
        plumbing and **raises** on persistent failure instead of degrading to a
        skipped turn — the judge (TICKET-5) needs a hard error so its repair loop
        can surface it. Transient API failures are retried with exponential backoff;
        the last error is re-raised once retries are exhausted.
        """
        model = self.model_for(tier)
        payload = [{"role": m.role, "content": m.content} for m in messages]

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
                    "completion attempt failed: tier=%s attempt=%s/%s error=%s",
                    tier,
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
                if attempt < self._max_retries:
                    self._sleep(self._backoff_base * (2**attempt))
                    continue
                raise

            latency_ms = (time.perf_counter() - start) * 1000
            text = "".join(block.text for block in response.content if block.type == "text")
            usage = getattr(response, "usage", None)
            tokens_in = getattr(usage, "input_tokens", 0) or 0
            tokens_out = getattr(usage, "output_tokens", 0) or 0
            logger.info(
                "completion ok: tier=%s model=%s tokens_in=%s tokens_out=%s latency_ms=%.1f",
                tier,
                model,
                tokens_in,
                tokens_out,
                latency_ms,
            )
            return text

        raise last_error  # pragma: no cover - loop either returns or raises above


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Lazy singleton so the client is only built when first needed."""
    global _service
    if _service is None:
        _service = LLMService()
    return _service
