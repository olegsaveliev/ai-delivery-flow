from types import SimpleNamespace
from unittest.mock import MagicMock

from anthropic import APIConnectionError

from app.core.guardrails import DebateGuardrails
from app.schemas.chat import ChatMessage
from app.services.llm import LLMService


def make_service() -> LLMService:
    """LLMService with a mocked Anthropic client and no real sleeping."""
    service = LLMService(sleep=lambda _: None)
    service._client = MagicMock()
    return service


def make_response(text: str = "hello", tokens_in: int = 12, tokens_out: int = 34):
    """Minimal stand-in for an Anthropic Messages response."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


def make_guardrails(**overrides) -> DebateGuardrails:
    defaults = dict(max_personas=3, max_rounds=2, max_tokens_per_debate=100_000)
    defaults.update(overrides)
    return DebateGuardrails(**defaults)


def transient_error() -> APIConnectionError:
    return APIConnectionError(message="boom", request=None)


MESSAGES = [ChatMessage(role="user", content="Should I take the job?")]


def test_model_tier_selection_from_config():
    service = make_service()
    assert service.model_for("personas") == "claude-sonnet-5"
    assert service.model_for("judge") == "claude-opus-4-8"
    assert service.model_for("utility") == "claude-haiku-4-5-20251001"


def test_cap_exceeded_blocks_call_and_returns_skipped():
    service = make_service()
    guard = make_guardrails(max_personas=3)

    result = service.run_turn(
        tier="personas",
        messages=MESSAGES,
        guardrails=guard,
        round_number=1,
        persona_index=3,  # beyond the persona cap
    )

    assert result.status == "skipped"
    assert result.reason is not None and "guardrail" in result.reason
    # Blocked BEFORE the call: the client was never invoked.
    service._client.messages.create.assert_not_called()


def test_transient_error_is_retried_then_succeeds():
    service = make_service()
    guard = make_guardrails()
    service._client.messages.create.side_effect = [transient_error(), make_response()]

    result = service.run_turn(
        tier="personas",
        messages=MESSAGES,
        guardrails=guard,
        round_number=1,
        persona_index=0,
    )

    assert result.status == "ok"
    assert result.text == "hello"
    assert result.tokens_in == 12
    assert result.tokens_out == 34
    assert service._client.messages.create.call_count == 2
    # Usage from the successful attempt is recorded against the debate budget.
    assert guard.tokens_spent == 46


def test_persistent_error_returns_skipped_after_retries():
    service = make_service()
    guard = make_guardrails()
    service._client.messages.create.side_effect = transient_error()

    result = service.run_turn(
        tier="judge",
        messages=MESSAGES,
        guardrails=guard,
        round_number=1,
        persona_index=0,
    )

    assert result.status == "skipped"
    assert result.reason is not None and "llm error" in result.reason
    # Default llm_max_retries=2 → 3 attempts total.
    assert service._client.messages.create.call_count == 3
    assert guard.tokens_spent == 0


def test_successful_turn_uses_tier_model_and_records_usage():
    service = make_service()
    guard = make_guardrails()
    service._client.messages.create.return_value = make_response()

    result = service.run_turn(
        tier="judge",
        messages=MESSAGES,
        guardrails=guard,
        round_number=2,
        persona_index=0,
    )

    assert result.status == "ok"
    assert result.model == "claude-opus-4-8"
    _, kwargs = service._client.messages.create.call_args
    assert kwargs["model"] == "claude-opus-4-8"
    assert guard.tokens_spent == 46
