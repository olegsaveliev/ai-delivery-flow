import asyncio
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.enums import Archetype, DebateStatus, TurnStatus
from app.repositories import debates as repo
from app.schemas.debate import PersonaOut
from app.schemas.events import (
    EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    DebateEvent,
    DoneEvent,
    ErrorEvent,
    PersonasAssignedEvent,
    RoundCompletedEvent,
    SequencedEvent,
    TurnCompletedEvent,
    TurnDeltaEvent,
    TurnStartedEvent,
    VerdictEvent,
    parse_event,
)
from app.schemas.verdict import Case, Verdict
from app.services.events import EventSequencer, EventSink, InMemoryEventSink, encode_sse

SPEC_EVENT_TYPES = {
    "personas_assigned",
    "turn_started",
    "turn_delta",
    "turn_completed",
    "round_completed",
    "verdict",
    "done",
    "error",
}

TS_TYPES_FILE = Path(__file__).resolve().parents[2] / "frontend/src/types/debateEvents.ts"

VERDICT = Verdict(
    recommendation="Adopt SQLite for the MVP",
    cases=[
        Case(option="SQLite", argument="Zero-config and local"),
        Case(option="Postgres", argument="Scales later"),
    ],
    tradeoffs=["Simplicity now vs. migration later"],
)

SAMPLES: list[DebateEvent] = [
    PersonasAssignedEvent(
        debate_id="d1",
        personas=[
            PersonaOut(id="p1", archetype=Archetype.ADVOCATE, name="The Advocate", stance="Yes"),
            PersonaOut(id="p2", archetype=Archetype.SKEPTIC, name="The Skeptic", stance="No"),
            PersonaOut(
                id="p3", archetype=Archetype.PRAGMATIST, name="The Pragmatist", stance="Depends"
            ),
        ],
    ),
    TurnStartedEvent(round=1, persona_id="p1"),
    TurnDeltaEvent(round=1, persona_id="p1", text="SQLite is "),
    TurnCompletedEvent(
        round=1, persona_id="p2", turn_id="t2", status=TurnStatus.SKIPPED, content=""
    ),
    RoundCompletedEvent(round=1),
    VerdictEvent(**VERDICT.model_dump()),
    DoneEvent(debate_id="d1", status=DebateStatus.COMPLETED),
    ErrorEvent(code="judge_failed", message="Verdict invalid after one repair"),
]


def _frame_lines(frame: str) -> list[str]:
    # Never splitlines(): it also splits on U+2028 etc., which SSE does not.
    return frame.split("\n")


def _data_of(frame: str) -> str:
    return _frame_lines(frame)[2].removeprefix("data: ")


async def _emit_all(sink: EventSink, events: list[DebateEvent]) -> None:
    for event in events:
        await sink.emit(event)


def test_samples_cover_every_event_type():
    assert len(EVENT_TYPES) == 8
    assert [e.type for e in SAMPLES] == list(EVENT_TYPES)


def test_event_set_is_exactly_the_outbound_contract():
    # DEC-004: server → client only; the union has no hidden/inbound variants.
    union = typing.get_args(DebateEvent)[0]
    tags = {model.model_fields["type"].default for model in typing.get_args(union)}
    assert set(EVENT_TYPES) == SPEC_EVENT_TYPES
    assert tags == SPEC_EVENT_TYPES
    assert TERMINAL_EVENT_TYPES == {"done", "error"}


@pytest.mark.parametrize("event", SAMPLES, ids=EVENT_TYPES)
def test_encode_sse_frame_format(event):
    frame = encode_sse(SequencedEvent(seq=7, event=event))

    assert frame.endswith("\n\n")
    assert "\r" not in frame
    assert _frame_lines(frame) == [
        "id: 7",
        f"event: {event.type}",
        f"data: {event.model_dump_json()}",
        "",
        "",
    ]


@pytest.mark.parametrize("event", SAMPLES, ids=EVENT_TYPES)
def test_data_round_trips_to_model(event):
    parsed = parse_event(_data_of(encode_sse(SequencedEvent(seq=1, event=event))))

    assert parsed == event
    assert type(parsed) is type(event)


def test_turn_delta_multiline_and_unicode_survive():
    text = "line one\nline two\r\n\ttabbed — “quotes” ünïcödé 🎉 中文   end"
    event = TurnDeltaEvent(round=2, persona_id="p3", text=text)

    frame = encode_sse(SequencedEvent(seq=3, event=event))
    lines = _frame_lines(frame)
    data = _data_of(frame)

    assert len(lines) == 5
    assert "\r" not in frame
    assert "\\n" in data
    assert "🎉" in data
    parsed = parse_event(data)
    assert isinstance(parsed, TurnDeltaEvent)
    assert parsed.text == text


def test_unknown_type_rejected():
    # Also the DEC-004 example: an inbound-looking type is not part of the contract.
    with pytest.raises(ValidationError):
        parse_event('{"type":"user_reply","text":"hi"}')


def test_missing_type_rejected():
    with pytest.raises(ValidationError):
        parse_event('{"round":1}')


def test_extra_field_rejected():
    # Also proves seq is not a payload field.
    with pytest.raises(ValidationError):
        parse_event('{"type":"round_completed","round":1,"seq":1}')


@pytest.mark.parametrize(
    "payload",
    [
        (
            '{"type":"turn_completed","round":1,"persona_id":"p","turn_id":"t",'
            '"status":"maybe","content":"x"}'
        ),
        '{"type":"turn_started","round":0,"persona_id":"p"}',
        '{"type":"verdict","recommendation":"x","cases":[],"tradeoffs":["t"]}',
        '{"type":"done","debate_id":"d","status":"exploded"}',
        '{"type":"error","code":"","message":"m"}',
    ],
    ids=["bad-enum", "round-zero", "empty-cases", "bad-debate-status", "empty-code"],
)
def test_invalid_payload_rejected(payload):
    with pytest.raises(ValidationError):
        parse_event(payload)


def test_events_are_frozen():
    event = RoundCompletedEvent(round=1)
    with pytest.raises(ValidationError):
        event.round = 2


@pytest.mark.parametrize(
    "status", [s for s in DebateStatus if s is not DebateStatus.COMPLETED], ids=str
)
def test_done_rejects_non_completed_status(status):
    # `done` is terminal success only; failures end with `error`.
    with pytest.raises(ValidationError):
        DoneEvent(debate_id="d", status=status)
    with pytest.raises(ValidationError):
        parse_event(f'{{"type":"done","debate_id":"d","status":"{status.value}"}}')


def test_done_status_defaults_to_completed():
    event = DoneEvent(debate_id="d")

    assert event.status is DebateStatus.COMPLETED
    assert '"status":"completed"' in event.model_dump_json()
    assert parse_event('{"type":"done","debate_id":"d","status":"completed"}') == event
    assert parse_event('{"type":"done","debate_id":"d"}') == event


def test_empty_turn_delta_text_is_accepted():
    assert TurnDeltaEvent(round=1, persona_id="p", text="").text == ""


def test_sequencer_is_monotonic_from_one():
    event = RoundCompletedEvent(round=1)
    sequencer = EventSequencer()

    assert [sequencer.stamp(event).seq for _ in range(5)] == [1, 2, 3, 4, 5]
    assert sequencer.last == 5
    assert EventSequencer(start=10).stamp(event).seq == 11
    with pytest.raises(ValueError):
        EventSequencer(start=-1)
    with pytest.raises(ValidationError):
        SequencedEvent(seq=0, event=event)


def test_sequencers_are_independent_per_debate():
    event = RoundCompletedEvent(round=1)
    a, b = EventSequencer(), EventSequencer()

    seqs = [(a.stamp(event).seq, b.stamp(event).seq) for _ in range(3)]

    assert seqs == [(1, 1), (2, 2), (3, 3)]


def test_in_memory_sink_records_in_order_with_seq():
    sink = InMemoryEventSink()
    assert isinstance(sink, EventSink)

    asyncio.run(_emit_all(sink, SAMPLES))

    assert sink.events == SAMPLES
    assert [f.seq for f in sink.frames] == list(range(1, 9))
    assert sink.types == list(EVENT_TYPES)
    frames = [f for f in sink.encoded().split("\n\n") if f]
    assert len(frames) == 8
    assert [f.split("\n")[0] for f in frames] == [f"id: {i}" for i in range(1, 9)]


def test_personas_assigned_accepts_orm_rows(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    for archetype in Archetype:
        repo.add_persona(session, debate.id, archetype, f"The {archetype.value}", "stance")
    session.commit()
    debate = repo.get_debate(session, debate.id)

    event = PersonasAssignedEvent(debate_id=debate.id, personas=debate.personas)

    assert [p.id for p in event.personas] == [p.id for p in debate.personas]
    assert {p.archetype for p in event.personas} == set(Archetype)
    assert parse_event(_data_of(encode_sse(SequencedEvent(seq=1, event=event)))) == event


def test_verdict_event_from_verdict_schema():
    event = VerdictEvent(**VERDICT.model_dump())

    parsed = parse_event(_data_of(encode_sse(SequencedEvent(seq=6, event=event))))

    assert parsed == event
    assert Verdict.model_validate(parsed.model_dump(exclude={"type"})) == VERDICT


def test_typescript_mirror_declares_every_event_type():
    ts = TS_TYPES_FILE.read_text(encoding="utf-8")
    for event_type in EVENT_TYPES:
        assert f'type: "{event_type}"' in ts, f"{event_type} missing from {TS_TYPES_FILE.name}"
