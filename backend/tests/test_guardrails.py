import pytest

from app.core.guardrails import CapExceededError, DebateGuardrails, guardrails_from_settings


def make_guardrails(**overrides) -> DebateGuardrails:
    defaults = dict(max_personas=3, max_rounds=2, max_tokens_per_debate=1000)
    defaults.update(overrides)
    return DebateGuardrails(**defaults)


def test_check_passes_within_caps():
    guard = make_guardrails()
    # Should not raise for the last allowed persona / round and affordable tokens.
    guard.check(round_number=2, persona_index=2, estimated_tokens=500)


def test_persona_cap_blocks():
    guard = make_guardrails()
    with pytest.raises(CapExceededError, match="persona cap"):
        guard.check(round_number=1, persona_index=3, estimated_tokens=0)


def test_round_cap_blocks():
    guard = make_guardrails()
    with pytest.raises(CapExceededError, match="round cap"):
        guard.check(round_number=3, persona_index=0, estimated_tokens=0)


def test_token_cap_blocks_on_projection():
    guard = make_guardrails(max_tokens_per_debate=1000)
    guard.record_usage(tokens_in=600, tokens_out=300)  # 900 spent
    with pytest.raises(CapExceededError, match="token cap"):
        guard.check(round_number=1, persona_index=0, estimated_tokens=200)  # 1100 > 1000


def test_record_usage_accumulates():
    guard = make_guardrails()
    guard.record_usage(tokens_in=100, tokens_out=50)
    guard.record_usage(tokens_in=10, tokens_out=5)
    assert guard.tokens_spent == 165


def test_guardrails_from_settings_uses_config_defaults():
    guard = guardrails_from_settings()
    assert guard.max_personas == 3
    assert guard.max_rounds == 2
    assert guard.max_tokens_per_debate == 200_000
    assert guard.tokens_spent == 0
