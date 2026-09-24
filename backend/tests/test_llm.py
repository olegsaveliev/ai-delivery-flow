from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anthropic import APIConnectionError, APIError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from app.core.config import Settings
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


def test_complete_returns_text_and_routes_to_tier_model():
    service = make_service()
    service._client.messages.create.return_value = make_response(text="verdict json")

    text = service.complete(tier="judge", messages=MESSAGES, system="be a judge")

    assert text == "verdict json"
    _, kwargs = service._client.messages.create.call_args
    assert kwargs["model"] == "claude-opus-4-8"  # DEC-007 judge tier
    assert kwargs["system"] == "be a judge"


def test_complete_raises_after_persistent_error():
    service = make_service()
    service._client.messages.create.side_effect = transient_error()

    with pytest.raises(APIError):
        service.complete(tier="judge", messages=MESSAGES)

    # Default llm_max_retries=2 -> 3 attempts, then re-raise (does NOT skip).
    assert service._client.messages.create.call_count == 3


# --- stream_turn (KAN-18) ----------------------------------------------------


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStream:
    """Stand-in for anthropic's MessageStream (what ``with ...stream() as s`` yields)."""

    def __init__(
        self,
        deltas,
        *,
        error=None,
        tokens_in=12,
        tokens_out=34,
        clock=None,
        advance_after=None,
        advance_by=0.0,
        snapshot_usage=None,
    ):
        self._deltas = list(deltas)
        self._error = error
        self._clock = clock
        self._advance_after = advance_after
        self._advance_by = advance_by
        self._usage = SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out)
        # (tokens_in, tokens_out) billed so far, or None = message_start not yet seen.
        self._snapshot_usage = snapshot_usage
        self.closed = False

    @property
    def text_stream(self):
        for i, delta in enumerate(self._deltas):
            if self._clock is not None and self._advance_after == i:
                self._clock.now += self._advance_by  # simulate slow generation
            yield delta
        if self._error is not None:
            raise self._error  # mid-stream (or pre-delta when there are no deltas)

    @property
    def current_message_snapshot(self):
        if self._snapshot_usage is None:
            raise AssertionError  # the real SDK asserts before message_start
        tokens_in, tokens_out = self._snapshot_usage
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out)
        )

    def get_final_message(self):
        return SimpleNamespace(usage=self._usage)


class FakeStreamManager:
    """Context manager returned by ``client.messages.stream(...)``."""

    def __init__(self, stream=None, *, enter_error=None, clock=None, enter_takes=0.0):
        self._stream = stream
        self._enter_error = enter_error
        self._clock = clock
        self._enter_takes = enter_takes

    def __enter__(self):
        if self._clock is not None:
            self._clock.now += self._enter_takes  # time spent sending / awaiting headers
        if self._enter_error is not None:
            raise self._enter_error  # the real SDK sends the HTTP request in __enter__
        return self._stream

    def __exit__(self, *exc):
        if self._stream is not None:
            self._stream.closed = True
        return False


def make_stream_service(clock=None, sleep=None) -> LLMService:
    """LLMService with a mocked client, no real sleeping, and a fake clock."""
    service = LLMService(
        sleep=sleep if sleep is not None else (lambda _: None),
        clock=clock if clock is not None else FakeClock(),
    )
    service._client = MagicMock()
    # stream_turn streams through the retry-disabled client built in __init__; point it
    # at the same mock so tests configure `service._client.messages.stream`.
    service._stream_client = service._client
    return service


def stream_kwargs(guard, on_delta, **overrides):
    kwargs = {
        "on_delta": on_delta,
        "messages": MESSAGES,
        "guardrails": guard,
        "round_number": 1,
        "persona_index": 0,
    }
    kwargs.update(overrides)
    return kwargs


def status_error(status: int, body=None, headers=None) -> APIStatusError:
    """APIStatusError as the SDK builds it (a 200 status = a mid-stream error event)."""
    response = MagicMock(status_code=status, headers=headers or {})
    return APIStatusError("boom", response=response, body=body)


OVERLOADED_EVENT = {"type": "error", "error": {"type": "overloaded_error"}}


def test_stream_turn_happy_path_emits_deltas_in_order():
    service = make_stream_service()
    guard = make_guardrails()
    stream = FakeStream(["Take ", "the ", "job."])
    service._client.messages.stream.return_value = FakeStreamManager(stream)
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append))

    assert result.status == "ok"
    assert received == ["Take ", "the ", "job."]
    assert result.text == "".join(received)
    assert result.tokens_in == 12
    assert result.tokens_out == 34
    assert guard.tokens_spent == 46
    assert result.model == "claude-sonnet-5"
    _, kwargs = service._client.messages.stream.call_args
    assert kwargs["model"] == "claude-sonnet-5"  # DEC-007 persona tier by default
    assert kwargs["timeout"].read == 60.0  # full budget left on the first attempt
    assert kwargs["timeout"].connect == 5.0
    assert "system" not in kwargs
    assert stream.closed is True


def test_stream_client_has_sdk_retries_disabled_and_shares_the_pool():
    service = LLMService()  # real Anthropic clients, no network call is made

    assert service._stream_client.max_retries == 0
    assert service._client.max_retries == 2  # run_turn/complete keep the SDK default
    assert service._stream_client._client is service._client._client  # same HTTP pool


def test_stream_turn_passes_system_prompt():
    service = make_stream_service()
    service._client.messages.stream.return_value = FakeStreamManager(FakeStream(["ok"]))

    service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None, system="be the skeptic"))

    _, kwargs = service._client.messages.stream.call_args
    assert kwargs["system"] == "be the skeptic"


def test_stream_turn_guardrail_block_makes_no_call_and_no_deltas():
    service = make_stream_service()
    guard = make_guardrails(max_personas=3)
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append, persona_index=3))

    assert result.status == "skipped"
    assert result.reason is not None and "guardrail" in result.reason
    assert received == []
    service._client.messages.stream.assert_not_called()


def test_stream_turn_retries_transient_error_before_first_delta():
    sleeps: list[float] = []
    service = make_stream_service(sleep=sleeps.append)
    guard = make_guardrails()
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error()),
        FakeStreamManager(FakeStream(["hi"])),
    ]
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append))

    assert result.status == "ok"
    assert result.text == "hi"
    assert received == ["hi"]
    assert service._client.messages.stream.call_count == 2
    assert sleeps == [0.5]  # backoff_base * 2**0
    assert guard.tokens_spent == 46


def test_stream_turn_persistent_pre_delta_failure_skips_after_retries():
    service = make_stream_service()
    guard = make_guardrails()
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error()) for _ in range(3)
    ]
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append))

    assert result.status == "skipped"
    assert result.reason is not None and "llm error after 3 attempts" in result.reason
    assert service._client.messages.stream.call_count == 3
    assert received == []
    assert guard.tokens_spent == 0


def test_stream_turn_failure_mid_stream_is_not_retried_and_skips():
    service = make_stream_service()
    guard = make_guardrails()
    service._client.messages.stream.side_effect = [
        FakeStreamManager(FakeStream(["partial ", "text"], error=transient_error())),
        FakeStreamManager(FakeStream(["never used"])),
    ]
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append))

    assert result.status == "skipped"
    assert result.reason is not None and "stream interrupted after 2 deltas" in result.reason
    assert received == ["partial ", "text"]  # emitted once, never duplicated
    assert result.text == ""
    assert service._client.messages.stream.call_count == 1
    assert guard.tokens_spent == 0  # no snapshot usage known


def test_stream_turn_on_delta_exception_is_contained():
    service = make_stream_service()
    service._client.messages.stream.return_value = FakeStreamManager(FakeStream(["a", "b"]))

    def on_delta(_delta: str) -> None:
        raise RuntimeError("consumer bug")

    result = service.stream_turn(**stream_kwargs(make_guardrails(), on_delta))

    assert result.status == "skipped"
    assert result.reason is not None and "stream interrupted" in result.reason
    assert service._client.messages.stream.call_count == 1


def test_stream_turn_timeout_mid_stream_skips_with_reason_timeout():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    guard = make_guardrails()
    # Deadline = 0 + 60. "a" arrives at t=0; the clock jumps to 61 before "b" arrives.
    stream = FakeStream(["a", "b", "c"], clock=clock, advance_after=1, advance_by=61.0)
    service._client.messages.stream.return_value = FakeStreamManager(stream)
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(guard, received.append))

    assert result.status == "skipped"
    assert result.reason == "timeout"
    assert received == ["a"]  # nothing emitted past the deadline
    assert result.text == ""
    assert service._client.messages.stream.call_count == 1
    assert stream.closed is True
    assert guard.tokens_spent == 0


def test_stream_turn_connect_timeout_with_budget_left_is_retried():
    service = make_stream_service()  # clock never moves: plenty of budget left
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=APITimeoutError(request=None)),
        FakeStreamManager(FakeStream(["hi"])),
    ]
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(make_guardrails(), received.append))

    assert result.status == "ok"
    assert received == ["hi"]
    assert service._client.messages.stream.call_count == 2


def test_stream_turn_request_timeout_at_the_deadline_is_timeout_not_retried():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    service._client.messages.stream.side_effect = [
        # The request used the whole budget waiting for headers, then timed out.
        FakeStreamManager(enter_error=APITimeoutError(request=None), clock=clock, enter_takes=60),
        FakeStreamManager(FakeStream(["never used"])),
    ]
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(make_guardrails(), received.append))

    assert result.status == "skipped"
    assert result.reason == "timeout"
    assert service._client.messages.stream.call_count == 1
    assert received == []


def test_stream_turn_raw_read_timeout_after_deadline_classified_as_timeout():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    # Stand-in for a raw httpx2.ReadTimeout raised by a stalled read after the deadline.
    stream = FakeStream(["a"], error=TimeoutError("read timed out"))
    service._client.messages.stream.return_value = FakeStreamManager(stream)
    received: list[str] = []

    def on_delta(delta: str) -> None:
        received.append(delta)
        clock.now += 61  # the stall that follows this delta blows the budget

    result = service.stream_turn(**stream_kwargs(make_guardrails(), on_delta))

    assert result.status == "skipped"
    assert result.reason == "timeout"  # not "stream interrupted"
    assert received == ["a"]
    assert service._client.messages.stream.call_count == 1


def test_stream_turn_error_past_deadline_keeps_real_cause():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    stream = FakeStream(["a"], error=status_error(200, OVERLOADED_EVENT))
    service._client.messages.stream.return_value = FakeStreamManager(stream)

    def on_delta(_delta: str) -> None:
        clock.now += 61  # the clock is past the deadline when the API error arrives

    result = service.stream_turn(**stream_kwargs(make_guardrails(), on_delta))

    assert result.reason is not None
    assert result.reason.startswith("stream interrupted after 1 deltas")  # not "timeout"


def test_run_turn_does_not_use_streaming():
    service = make_service()
    service._client.messages.create.return_value = make_response()

    result = service.run_turn(
        tier="personas",
        messages=MESSAGES,
        guardrails=make_guardrails(),
        round_number=1,
        persona_index=0,
    )

    assert result.status == "ok"
    service._client.messages.stream.assert_not_called()


def test_stream_turn_deadline_passing_during_backoff_times_out_without_new_call():
    clock = FakeClock()
    service = make_stream_service(clock=clock, sleep=lambda _s: setattr(clock, "now", 61.0))
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error()),
        FakeStreamManager(FakeStream(["never used"])),
    ]

    result = service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    assert result.status == "skipped"
    assert result.reason == "timeout"
    assert service._client.messages.stream.call_count == 1


def test_stream_turn_backoff_never_sleeps_past_the_deadline():
    clock = FakeClock()
    sleeps: list[float] = []
    service = make_stream_service(clock=clock, sleep=sleeps.append)
    # Budget 60 s; the failed attempt itself took 59.8 s.
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error(), clock=clock, enter_takes=59.8),
        FakeStreamManager(FakeStream(["hi"])),
    ]

    service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    assert sleeps == [pytest.approx(0.2)]  # capped at the remaining budget, not 0.5


def test_stream_turn_each_attempt_gets_only_the_remaining_budget():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error(), clock=clock, enter_takes=10.0),
        FakeStreamManager(FakeStream(["hi"])),
    ]

    result = service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    assert result.status == "ok"
    first, second = service._client.messages.stream.call_args_list
    assert first.kwargs["timeout"].read == 60.0
    assert second.kwargs["timeout"].read == 50.0  # shrinks with the budget
    assert second.kwargs["timeout"].connect == 5.0


def test_stream_turn_connect_timeout_never_exceeds_remaining_budget():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=transient_error(), clock=clock, enter_takes=57.0),
        FakeStreamManager(FakeStream(["hi"])),
    ]

    service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    second = service._client.messages.stream.call_args_list[1]
    # 3 s left (the injected sleep doesn't move the clock): connect is capped by it.
    assert second.kwargs["timeout"].connect == pytest.approx(3.0)
    assert second.kwargs["timeout"].read == pytest.approx(3.0)


@pytest.mark.parametrize(
    "error",
    [
        status_error(400),
        status_error(401),
        status_error(404),
        status_error(200, {"type": "error", "error": {"type": "invalid_request_error"}}),
        status_error(500, headers={"x-should-retry": "false"}),
        TypeError("bug in our code"),
        ValueError("bad payload"),
    ],
    ids=[
        "400",
        "401",
        "404",
        "event-invalid_request",
        "500-x-should-retry-false",
        "TypeError",
        "ValueError",
    ],
)
def test_stream_turn_non_transient_pre_delta_error_skips_without_retry(error):
    sleeps: list[float] = []
    service = make_stream_service(sleep=sleeps.append)
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=error),
        FakeStreamManager(FakeStream(["never used"])),
    ]

    result = service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    assert result.status == "skipped"
    assert result.reason is not None
    assert result.reason.startswith("non-retryable error before first delta")
    assert type(error).__name__ in result.reason
    assert service._client.messages.stream.call_count == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error",
    [
        status_error(408),
        status_error(409),
        status_error(429),
        status_error(500),
        status_error(529),
        status_error(400, headers={"x-should-retry": "true"}),
        status_error(200, OVERLOADED_EVENT),
        status_error(200, {"type": "error", "error": {"type": "api_error"}}),
        transient_error(),
        APITimeoutError(request=None),
        ConnectionResetError("reset"),
    ],
    ids=[
        "408",
        "409",
        "429",
        "500",
        "529",
        "400-x-should-retry-true",
        "event-overloaded",
        "event-api_error",
        "APIConnectionError",
        "APITimeoutError-budget-left",
        "OSError",
    ],
)
def test_stream_turn_transient_pre_delta_error_is_retried(error):
    service = make_stream_service()
    service._client.messages.stream.side_effect = [
        FakeStreamManager(enter_error=error),
        FakeStreamManager(FakeStream(["hi"])),
    ]

    result = service.stream_turn(**stream_kwargs(make_guardrails(), lambda _: None))

    assert result.status == "ok"
    assert service._client.messages.stream.call_count == 2


def test_stream_turn_retried_attempt_charges_the_input_it_was_billed():
    service = make_stream_service()
    guard = make_guardrails()
    service._client.messages.stream.side_effect = [
        # message_start arrived (10 in, 1 out), then an overloaded event before any text.
        FakeStreamManager(
            FakeStream([], error=status_error(200, OVERLOADED_EVENT), snapshot_usage=(10, 1))
        ),
        FakeStreamManager(FakeStream(["hi"])),  # final usage 12 + 34
    ]

    result = service.stream_turn(**stream_kwargs(guard, lambda _: None))

    assert result.status == "ok"
    assert guard.tokens_spent == 11 + 46  # both attempts' billed tokens count


def test_stream_turn_mid_stream_skip_records_partial_usage():
    service = make_stream_service()
    guard = make_guardrails()
    stream = FakeStream(["partial"], error=transient_error(), snapshot_usage=(10, 3))
    service._client.messages.stream.return_value = FakeStreamManager(stream)

    result = service.stream_turn(**stream_kwargs(guard, lambda _: None))

    assert result.status == "skipped"
    assert result.reason is not None and result.reason.startswith("stream interrupted")
    assert (result.tokens_in, result.tokens_out) == (10, 3)
    assert guard.tokens_spent == 13  # billed tokens charged to the debate cap


def test_stream_turn_timeout_records_partial_usage():
    clock = FakeClock()
    service = make_stream_service(clock=clock)
    guard = make_guardrails()
    stream = FakeStream(
        ["a", "b"], clock=clock, advance_after=1, advance_by=61.0, snapshot_usage=(10, 2)
    )
    service._client.messages.stream.return_value = FakeStreamManager(stream)

    result = service.stream_turn(**stream_kwargs(guard, lambda _: None))

    assert result.reason == "timeout"
    assert (result.tokens_in, result.tokens_out) == (10, 2)
    assert guard.tokens_spent == 12


def test_stream_turn_skip_before_message_start_records_no_usage():
    service = make_stream_service()
    guard = make_guardrails()
    # Snapshot unavailable (the SDK asserts before message_start): must not raise.
    stream = FakeStream(["x"], error=transient_error(), snapshot_usage=None)
    service._client.messages.stream.return_value = FakeStreamManager(stream)

    result = service.stream_turn(**stream_kwargs(guard, lambda _: None))

    assert result.status == "skipped"
    assert (result.tokens_in, result.tokens_out) == (0, 0)
    assert guard.tokens_spent == 0


def test_stream_turn_skips_empty_deltas():
    service = make_stream_service()
    stream = FakeStream(["", "a", "", "b", ""])
    service._client.messages.stream.return_value = FakeStreamManager(stream)
    received: list[str] = []

    result = service.stream_turn(**stream_kwargs(make_guardrails(), received.append))

    assert received == ["a", "b"]
    assert result.text == "ab"


@pytest.mark.parametrize("value", [0, -1])
def test_turn_timeout_seconds_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(turn_timeout_seconds=value)
