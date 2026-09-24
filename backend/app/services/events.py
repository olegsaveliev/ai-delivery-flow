"""SSE wire encoding and the event-sink abstraction (EPIC-B B-T1 / KAN-17).

One Server-Sent Events frame per debate event
(https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)::

    id: <seq>
    event: <type>
    data: <single-line JSON of the event, including "type">
    <blank line>

LF line endings only. ``seq`` is per-debate, starts at 1, and increases by 1 per event; it
is assigned by the sink that owns the debate's stream, never by producers. The broker and
``GET /api/debates/{id}/stream`` endpoint are B-T4 (gated on DEC-012) and do not live here.
"""

from typing import Protocol, runtime_checkable

from app.schemas.events import DebateEvent, SequencedEvent


class EventSequencer:
    """Per-debate monotonic sequence counter.

    Use one per debate. The first ``stamp`` returns ``start + 1`` (1 by default); ``start``
    lets a broker continue an existing sequence. Not thread-safe: use it only on the
    event-loop thread (producers bridge worker-thread deltas onto the loop first).
    """

    def __init__(self, start: int = 0) -> None:
        if start < 0:
            raise ValueError("start must be >= 0")
        self._last = start

    @property
    def last(self) -> int:
        """The last issued ``seq`` (``start`` if nothing has been stamped yet)."""
        return self._last

    def stamp(self, event: DebateEvent) -> SequencedEvent:
        self._last += 1
        return SequencedEvent(seq=self._last, event=event)


def encode_sse(item: SequencedEvent) -> str:
    """Encode one sequenced event as an SSE frame.

    ``data`` is always one line: Pydantic's JSON escapes CR/LF. Non-ASCII stays raw UTF-8;
    the transport encodes the returned ``str`` to bytes.
    """
    data = item.event.model_dump_json()
    return f"id: {item.seq}\nevent: {item.event.type}\ndata: {data}\n\n"


@runtime_checkable
class EventSink(Protocol):
    """Where producers (the orchestrator, B-T3) write bare debate events.

    The sink owns per-debate sequencing. Sinks must not raise on a valid event.
    """

    async def emit(self, event: DebateEvent) -> None: ...


class InMemoryEventSink:
    """Test double and reference ``EventSink``: records stamped frames in emission order."""

    def __init__(self) -> None:
        self._sequencer = EventSequencer()
        self.frames: list[SequencedEvent] = []

    async def emit(self, event: DebateEvent) -> None:
        self.frames.append(self._sequencer.stamp(event))

    @property
    def events(self) -> list[DebateEvent]:
        return [frame.event for frame in self.frames]

    @property
    def types(self) -> list[str]:
        return [frame.event.type for frame in self.frames]

    def encoded(self) -> str:
        """The whole recorded stream as SSE text."""
        return "".join(encode_sse(frame) for frame in self.frames)
