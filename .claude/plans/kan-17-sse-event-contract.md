# Feature: KAN-17 (EPIC-B · B-T1) — SSE event contract & wire encoding

The following plan should be complete, but it's important that you validate documentation and
codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files.

## Feature Description

Freeze the **debate stream contract** for EPIC-B (Live Streaming, KAN-2): one typed Pydantic model
per stream event (8 in total), discriminated by a `type` literal. The ticket also adds a
per-debate **sequence counter**, an `encode_sse()` helper that turns a sequenced event into a
spec-compliant Server-Sent Events frame, an `EventSink` protocol that producers write to, an
in-memory sink for tests, and a matching TypeScript mirror for the future UI (EPIC-C).

The ticket is pure contract and encoding. Nothing emits events yet: the orchestrator does that in
KAN-19 (B-T3), and the endpoint/broker come in B-T4, which is blocked on Proposed DEC-012.

## User Story

As a developer
I want one typed, frozen definition of every debate stream event and its SSE wire format
So that the orchestrator, the endpoint, and the future UI all agree on the stream without guessing.

## Problem Statement

EPIC-B needs three separately built parts to agree on a byte-level stream: the orchestrator
(B-T3), the SSE endpoint and broker (B-T4/B-T5), and the React client (EPIC-C). If the event names,
payload fields, sequencing and framing are not frozen first, each part will guess, and the pieces
will drift. There is no event type, SSE encoder or sink abstraction in the codebase today.

## Solution Statement

1. `backend/app/schemas/events.py` holds 8 frozen Pydantic v2 models (`extra="forbid"`), each with
   a `type: Literal[...]` discriminator. It also holds the `DebateEvent` discriminated union, a
   module-level `TypeAdapter` plus a `parse_event()` helper (unknown or missing `type` → rejected),
   the `EVENT_TYPES` / `TERMINAL_EVENT_TYPES` constants, and a `SequencedEvent` envelope
   `{seq, event}`.
2. `backend/app/services/events.py` holds `EventSequencer` (a per-debate monotonic counter that
   stamps events into `SequencedEvent`s), `encode_sse(item)` (produces the frame
   `id: <seq>\nevent: <type>\ndata: <json>\n\n`), the `EventSink` protocol (`async emit(event)`),
   and `InMemoryEventSink` (owns one sequencer and records frames).
3. `frontend/src/types/debateEvents.ts` holds matching plain TS interfaces plus a `DebateEvent`
   union. It is types only.
4. `backend/tests/test_events.py` tests encoding for every type, the newline/unicode round-trip,
   rejection cases, sequencing, the sink, and a Python↔TS drift guard.

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: Low
**Primary Systems Affected**: `backend/app/schemas` (new `events`), `backend/app/services` (new
`events`), `frontend/src/types` (new `debateEvents.ts`). No routes, no DB, no orchestrator change.
**Dependencies**: none new. `pydantic` v2 (2.13 installed) and `typescript` ^5.7 (declared; see the
node_modules note in Validation).

### Governing decisions (Decision Log page 1015810, re-read 2026-09-24)

- **DEC-003 (Accepted), fixed 2 rounds.** The contract has a `round_completed{round}` event that
  the orchestrator emits once per round, so twice per debate. B-T1 constrains `round` to `>= 1` but
  does not hardcode `<= 2`. The round count is enforced by `DebateGuardrails.max_rounds` and the
  orchestrator, and hardcoding it would duplicate that rule in the wire schema. The "exactly 2" is
  asserted in B-T3's orchestrator tests.
- **DEC-004 (Accepted), watch-only.** The contract is **server → client only**. There are no
  inbound or client-originated event types in Python or TS, and the module docstrings say so.
  `parse_event()` exists for tests and for server-side replay (B-T4). It is **not** an input
  surface. A test asserts that the event set is exactly the 8 outbound types.
- **DEC-007 (Accepted), constraint honored.** The judge is not streamed. `verdict` is one event
  carrying the already-validated `Verdict` fields, and it reuses `schemas.verdict.Case`.
- **DEC-008 (Accepted), constraint honored.** `turn_completed` carries the persisted `turn_id`
  and full authoritative `content`, so the client can recover from missed deltas.
- **DEC-005 (Accepted).** Single-user and local. A plain in-process counter per debate is
  sufficient. There is no distributed sequencing.
- **DEC-011 (Accepted).** At `/commit` time, add the new modules to `docs/architecture.md`
  (Backend table + a short "Stream event contract" subsection + a Change Log row) and to
  Confluence 917506.
- **DEC-012 (Proposed, not relied on).** Nothing here depends on it. The envelope/sequencer
  split below keeps the broker's replay options open (for example, re-stamping replayed events
  or seeding a sequencer), so any outcome of DEC-012 fits on top.
- **DEC-010.** Does not apply. The ticket adds types only, with no UI or components.

**No contradiction with any Accepted DEC.** The wire-level choices below (envelope vs in-model
`seq`, seq starts at 1, `data` excludes `seq`, persona payload = `PersonaOut`) implement the event
contract table that the epic spec already froze. They are recorded in this plan and in module
docstrings, not as a new DEC. See the open questions in NOTES if the reviewer wants any promoted
to a DEC.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — READ THESE BEFORE IMPLEMENTING

- `/Users/oleh_saveliev/Desktop/Projects/AI-Delivery-Flow/docs/specs/epic-b-live-streaming.md`
  (lines 17–32 "Event contract", 36–47 "B-T1"). This is the frozen event table and the AC. The
  file is **not committed yet**, so read it from the main checkout by absolute path.
- `backend/app/models/enums.py` (lines 4–26). Reuse `Archetype`, `TurnStatus`, `DebateStatus`.
  All are `str, Enum`, so they serialize to their lowercase values (`"ok"`, `"skipped"`,
  `"completed"`, …).
  - **GOTCHA:** `backend/app/schemas/persona.py:6` defines a *second*, duplicate `Archetype` enum
    (used by `prompts/personas.py`). **Import from `app.models.enums`**, the same source that
    `schemas/debate.py:14` uses. Do not use `app.schemas.persona`.
- `backend/app/schemas/debate.py` (lines 1–35). The module docstring style (ticket + DEC cites),
  `PersonaOut` (lines 25–33: `id, archetype, name, stance`, `from_attributes=True`). `PersonaOut`
  is the persona payload of `personas_assigned` (see the design decision below).
- `backend/app/schemas/verdict.py` (lines 14–35). `Case` (option/argument) and the `Verdict` field
  shapes that `VerdictEvent` mirrors. Also `Tradeoff = Annotated[str, Field(min_length=1)]`.
- `backend/app/schemas/persona.py` (lines 14–28). The domain `Persona` has a UI `color` and **no
  `id`**. That is why it is *not* used here.
- `backend/app/services/llm.py`. Service-module docstring and naming style (e.g. `TurnResult`
  dataclass). No import from it is needed.
- `backend/tests/test_orchestrator.py` (lines 1–60). Test style: plain `def test_*` functions and
  **`asyncio.run(...)` for coroutines. `pytest-asyncio` is NOT installed**, so do not use
  `@pytest.mark.asyncio`.
- `frontend/src/types/chat.ts`. The TS convention: exported plain `interface`s with
  string-literal unions and no runtime code.
- `frontend/tsconfig.json`. `strict`, `noUnusedLocals`, `noUnusedParameters`, `isolatedModules`,
  `include: ["src"]`. **Export every type**, because an unexported unused type alias fails
  `noUnusedLocals`.
- `backend/pyproject.toml`. Ruff `line-length = 100`, `target-version = "py311"`, default rule
  set. pytest `pythonpath = ["."]`.

### New Files to Create

- `backend/app/schemas/events.py`. The 8 event models, the `DebateEvent` union, the adapter +
  `parse_event`, `EVENT_TYPES`, `TERMINAL_EVENT_TYPES`, and `SequencedEvent`.
- `backend/app/services/events.py`. `EventSequencer`, `encode_sse`, the `EventSink` protocol,
  and `InMemoryEventSink`.
- `backend/tests/test_events.py`. Unit tests.
- `frontend/src/types/debateEvents.ts`. The TS mirror (types only).

### Files to Modify

- None in code. (`docs/architecture.md` + Confluence 917506 are updated by `/commit` per DEC-011.
  See the Completion Checklist.)

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [WHATWG HTML — Server-sent events, §9.2.6 "Interpreting an event stream"](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)
  - Why: frame grammar. Fields are `field: value` lines, the only line terminators are
    `CRLF | LF | CR`, and a blank line dispatches the event. `id` sets `lastEventId`, and `event`
    sets the event name that `EventSource.addEventListener(<type>)` receives. A space after the
    colon is stripped once. The `id` value must not contain NUL.
- [Pydantic v2 — Discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)
  - Why: `Annotated[A | B | ..., Field(discriminator="type")]`, error types `union_tag_invalid`
    / `union_tag_not_found`.
- [Pydantic v2 — TypeAdapter](https://docs.pydantic.dev/latest/concepts/type_adapter/)
  - Why: parsing a union that is not a `BaseModel` (`validate_json`, `validate_python`).
- [Pydantic v2 — Model config `frozen` / `extra`](https://docs.pydantic.dev/latest/api/config/)

### Verified behavior (probed locally against pydantic 2.13.4 while planning)

- `model_dump_json()` output is compact, single-line JSON. `\n` and `\r` are escaped as `\\n` and
  `\\r`, and non-ASCII (`é`, `🎉`, ` `) is emitted **raw UTF-8, not `\u`-escaped**. This is
  safe for SSE, because only CR/LF terminate lines and U+2028 is not a terminator. It is also
  safe for `JSON.parse` (ES2019+).
- A discriminated union through `TypeAdapter.validate_json`:
  - `{"type":"z"}` → `ValidationError` (`union_tag_invalid`)
  - no `type` → `ValidationError` (`union_tag_not_found`)
  - an extra key with `extra="forbid"` → `ValidationError` (`extra_forbidden`)
- Assigning to a `frozen=True` model raises `ValidationError`.
- `PersonasAssignedEvent(personas=[<ORM Persona>])` validates **directly from ORM objects**,
  because `PersonaOut` has `from_attributes=True`. Extra ORM attributes such as `debate_id` are
  ignored, not rejected, since `extra="forbid"` is on the event and not on `PersonaOut`. B-T3 can
  therefore pass `debate.personas` straight in.

### Patterns to Follow

**Module docstring (from `schemas/debate.py:1-8`).** State the ticket + DECs and what the module
is and is not:
```python
"""Debate stream event contract (EPIC-B B-T1 / KAN-17, DEC-003/004).

The frozen, typed definition of every server-sent debate event. Server → client only
(DEC-004 watch-only): there are no inbound event types. ...
"""
```

**Pydantic style.** `BaseModel`, `Field(..., description=...)`, `ConfigDict`, enums from
`app.models.enums`.

**Tests.** Module-level sample builders, plain functions, `asyncio.run` for async, and
`pytest.mark.parametrize` for the per-type matrix.

---

## KEY DESIGN DECISIONS (concrete, decided)

1. **Where `seq` lives: in an envelope, not on the event.** The event models are
   payload-plus-`type` only. `SequencedEvent(seq: int ≥ 1, event: DebateEvent)` pairs an event
   with its position. `EventSequencer` (one per debate) produces those pairs. Producers (the
   orchestrator in B-T3) call `await sink.emit(event)` with a bare event and never see `seq`. The
   sink or broker owns the debate's sequencer and stamps events.
   *Why:* producers stay seq-agnostic, which keeps B-T3 simple and thread-bridging free of
   counters. There is one clear owner per debate, so the counter is monotonic by construction. The
   broker (B-T4) keeps full control over replay and live ordering under whatever DEC-012 settles.
   *AC mapping:* the AC's `encode_sse(event)` takes the **sequenced** event
   (`encode_sse(item: SequencedEvent)`), because the frame needs `id: <seq>`.
2. **`seq` starts at 1** and increments by exactly 1 per stamped event. 0 or absent means
   "nothing seen yet", so a future `Last-Event-ID: 0` is unambiguous. `EventSequencer(start=0)`
   accepts a seed (the last issued seq), so a broker can continue a sequence. `.last` exposes the
   last issued value.
3. **Exact frame format** (`str`, UTF-8 when encoded by the transport):
   ```
   id: <seq>\n
   event: <type>\n
   data: <event.model_dump_json()>\n
   \n
   ```
   The frame is `f"id: {seq}\nevent: {event.type}\ndata: {json}\n\n"`. It uses LF only, with no
   CR and no `retry:` field. Keepalive comments (`: ping`) belong to B-T5 and are out of scope.
   `data` is always **one line**, because Pydantic's JSON escapes CR/LF. It contains the full
   event **including `type`**, so `data` parses back to the model on its own. It does **not**
   repeat `seq`, because the client gets that from `MessageEvent.lastEventId`.
4. **Discriminated union exposure:**
   ```python
   DebateEvent = Annotated[
       PersonasAssignedEvent | TurnStartedEvent | TurnDeltaEvent | TurnCompletedEvent
       | RoundCompletedEvent | VerdictEvent | DoneEvent | ErrorEvent,
       Field(discriminator="type"),
   ]
   DEBATE_EVENT_ADAPTER: TypeAdapter[DebateEvent] = TypeAdapter(DebateEvent)
   def parse_event(data: str | bytes) -> DebateEvent: return DEBATE_EVENT_ADAPTER.validate_json(data)
   ```
5. **Strictness.** Every event model sets `ConfigDict(extra="forbid", frozen=True)`. This contract
   is server-authored, so an unexpected field means a bug or drift, and `frozen` makes an event
   immutable once emitted, which is safe to fan out to many subscribers. (This deliberately
   differs from `schemas.verdict.Verdict`, which ignores extras so that a harmless extra key from
   the LLM does not cost a repair retry. That concern does not apply here.)
6. **`personas_assigned` payload = `PersonaOut` (`id, archetype, name, stance`), not the domain
   `schemas.persona.Persona`.**
   - Every later event references `persona_id`, so the payload **must** carry the persisted `id`.
     The domain `Persona` has no id.
   - It matches the spec's indicative payload and the `GET /api/debates/{id}` aggregate
     (`DebateOut.personas`). The UI therefore sees one persona shape from REST and SSE, and B-T4
     replay can build the event straight from ORM rows (`from_attributes`).
   - `color` is **not persisted**, so a replayed `personas_assigned` could not supply it. It is a
     pure function of `archetype` (DEC-006 stable color key), so the client derives it from
     `archetype`. This keeps the wire free of UI concerns.
7. **`verdict` payload** is flat `{type, recommendation, cases: list[Case], tradeoffs: list[str]}`
   and mirrors `VerdictOut`. There is no nested `verdict` object, which matches the spec table.
   Constraints mirror `schemas.verdict.Verdict` (`min_length=1` on recommendation/cases, and
   `list[Tradeoff]`), because the event is built only from an already-validated `Verdict`.
   Builder: `VerdictEvent(**verdict.model_dump())`.
8. **Field constraints:**
   - `round: int = Field(..., ge=1)` (see DEC-003 note)
   - ids `str` with `min_length=1`
   - `TurnDeltaEvent.text: str` **with no min_length**. An empty chunk must never crash the hot
     streaming path. Producers *should* skip empty deltas, which is B-T3's concern.
   - `TurnCompletedEvent.content: str`, which may be empty (skipped turns persist `""`)
   - `ErrorEvent.code: str` (`min_length=1`) plus `message: str`
   - `DoneEvent.status: DebateStatus`
9. **`ErrorEvent.code` is an open `str`, not an enum.** The known codes (`judge_failed`,
   `timeout`, `internal`) come from B-T4/B-T5 and the Proposed DEC-012. Freezing an enum now would
   presume DEC-012's outcome. They are documented in the docstring as `KNOWN_ERROR_CODES` (a
   frozenset constant, informational). In TS: `code: string`.
10. **Sequencer thread-safety.** `EventSequencer` is **not** thread-safe by design. It must only be
    used on the event-loop thread. B-T3 bridges worker-thread deltas onto the loop
    (`call_soon_threadsafe` / `asyncio.Queue`) *before* `emit`. Document this in the docstring.

### Final event table (what gets implemented)

| `type` | Python model | Fields (besides `type`) |
| --- | --- | --- |
| `personas_assigned` | `PersonasAssignedEvent` | `debate_id: str`, `personas: list[PersonaOut]` (min_length=1) |
| `turn_started` | `TurnStartedEvent` | `round: int≥1`, `persona_id: str` |
| `turn_delta` | `TurnDeltaEvent` | `round: int≥1`, `persona_id: str`, `text: str` |
| `turn_completed` | `TurnCompletedEvent` | `round: int≥1`, `persona_id: str`, `turn_id: str`, `status: TurnStatus`, `content: str` |
| `round_completed` | `RoundCompletedEvent` | `round: int≥1` |
| `verdict` | `VerdictEvent` | `recommendation: str`, `cases: list[Case]`, `tradeoffs: list[str]` |
| `done` | `DoneEvent` | `debate_id: str`, `status: DebateStatus` |
| `error` | `ErrorEvent` | `code: str`, `message: str` |

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — the contract (`schemas/events.py`)
The 8 models, the union, the adapter and constants, and the `SequencedEvent` envelope.

### Phase 2: Core — encoding & sinks (`services/events.py`)
The sequencer, the `encode_sse` frame builder, the `EventSink` protocol, and the in-memory sink.

### Phase 3: Integration — TS mirror (`frontend/src/types/debateEvents.ts`)
Plain interfaces and the union, kept 1:1 with the Python table. No producer or consumer is wired
up in this ticket (B-T3/B-T4/EPIC-C).

### Phase 4: Testing & Validation
`test_events.py` (encoding matrix, round-trip, rejection, seq, sink, TS drift guard), then ruff,
the full pytest suite, and `tsc`.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### CREATE `backend/app/schemas/events.py`

- **IMPLEMENT**:
  - A module docstring: B-T1/KAN-17, DEC-003 (two `round_completed`), DEC-004 (server → client
    only, no inbound types), DEC-007 (verdict is a single non-streamed event), DEC-008
    (`turn_completed` is authoritative, after persistence). Explain that `seq` is carried by
    `SequencedEvent` and not by the events, and that `personas_assigned` uses `PersonaOut`
    (id-bearing, color derived client-side from archetype).
  - `class _StreamEvent(BaseModel)` with `model_config = ConfigDict(extra="forbid", frozen=True)`.
    It is a base only and has no `type` field.
  - The 8 subclasses, exactly as in the table above. Each has
    `type: Literal["<name>"] = "<name>"` as its **first** field, so it serializes first in `data`,
    plus a one-line docstring saying *when* it is emitted (copy the "When" column of the spec).
    Examples:
    ```python
    class TurnDeltaEvent(_StreamEvent):
        """A streamed text chunk of an in-progress turn (may contain newlines/unicode)."""

        type: Literal["turn_delta"] = "turn_delta"
        round: int = Field(..., ge=1, description="1-based round number (DEC-003: 2 rounds)")
        persona_id: str = Field(..., min_length=1)
        text: str = Field(..., description="Chunk text; producers should skip empty chunks")

    class VerdictEvent(_StreamEvent):
        """Judge verdict, persisted (DEC-007 — one non-streamed event)."""

        type: Literal["verdict"] = "verdict"
        recommendation: str = Field(..., min_length=1)
        cases: list[Case] = Field(..., min_length=1)
        tradeoffs: list[Tradeoff] = Field(..., min_length=1)
    ```
  - `DebateEvent = Annotated[<8-way | union>, Field(discriminator="type")]`
  - `DEBATE_EVENT_ADAPTER: TypeAdapter[DebateEvent] = TypeAdapter(DebateEvent)`
  - `def parse_event(data: str | bytes) -> DebateEvent`, which returns
    `DEBATE_EVENT_ADAPTER.validate_json(data)`. Docstring: raises `pydantic.ValidationError` on an
    unknown or missing `type` or a bad payload. It is for tests and server-side replay, **not** an
    inbound API (DEC-004).
  - `EVENT_TYPES: tuple[str, ...]` = the 8 names **in contract-table order**.
    `TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"done", "error"})`.
    `KNOWN_ERROR_CODES: frozenset[str] = frozenset({"judge_failed", "timeout", "internal"})`,
    commented as informational (from DEC-012 / B-T4-5, not enforced).
  - `class SequencedEvent(BaseModel)`: `model_config = ConfigDict(frozen=True)`;
    `seq: int = Field(..., ge=1)`; `event: DebateEvent`. Docstring: the per-debate position of an
    event, where `seq` becomes the SSE `id:`.
- **PATTERN**: `backend/app/schemas/debate.py:1-35` (docstring, enums import, `Field`),
  `backend/app/schemas/verdict.py:14-35` (`Tradeoff`, `Case`).
- **IMPORTS**: `from typing import Annotated, Literal`;
  `from pydantic import BaseModel, ConfigDict, Field, TypeAdapter`;
  `from app.models.enums import DebateStatus, TurnStatus`; `from app.schemas.debate import
  PersonaOut`; `from app.schemas.verdict import Case, Tradeoff`.
- **GOTCHA**:
  - Import `Archetype`/enums from `app.models.enums`, **not** `app.schemas.persona` (that module
    has a duplicate enum).
  - `type` and `round` shadow builtins, which is accepted: `TurnOut.round` already does it, and
    ruff's default rules do not flag it.
  - The `type` field needs a default (`= "<name>"`) so producers write
    `TurnStartedEvent(round=1, persona_id=pid)`. Keep it a `Literal`, not a plain `str`, or the
    discriminator will not work.
  - Do not add a `seq` field to any event model (Design Decision 1).
  - Do not modify `PersonaOut` or subclass it with `extra="forbid"`. Leave it untouched so that
    extra ORM attributes such as `debate_id` stay ignorable when events are built from rows.
- **VALIDATE**:
  `cd backend && python -c "from app.schemas.events import parse_event, TurnDeltaEvent as T; e=T(round=1, persona_id='p', text='a\nb'); assert parse_event(e.model_dump_json())==e; print('ok')"`

### CREATE `backend/app/services/events.py`

- **IMPLEMENT**:
  - A module docstring: SSE wire encoding + sink abstraction (B-T1/KAN-17). The frame format
    appears verbatim, with a link to the WHATWG SSE spec. Say that the broker/endpoint is B-T4
    (DEC-012) and is not here.
  - `class EventSequencer`:
    ```python
    def __init__(self, start: int = 0) -> None:
        if start < 0: raise ValueError("start must be >= 0")
        self._last = start
    @property
    def last(self) -> int: return self._last
    def stamp(self, event: DebateEvent) -> SequencedEvent:
        self._last += 1
        return SequencedEvent(seq=self._last, event=event)
    ```
    Docstring: one per debate; the first stamp is `start + 1` (1 by default); **not
    thread-safe**, so use it only on the event-loop thread (B-T3 bridges worker-thread deltas
    first).
  - `def encode_sse(item: SequencedEvent) -> str`:
    ```python
    data = item.event.model_dump_json()
    return f"id: {item.seq}\nevent: {item.event.type}\ndata: {data}\n\n"
    ```
    Docstring: one SSE frame; `data` is single-line JSON because Pydantic escapes CR/LF; UTF-8 is
    left raw; the transport encodes to bytes.
  - `@runtime_checkable class EventSink(Protocol)` with
    `async def emit(self, event: DebateEvent) -> None: ...`. Docstring: producers (the
    orchestrator, B-T3) emit bare events; the sink owns per-debate sequencing; sinks must not raise
    on a valid event.
  - `class InMemoryEventSink` (test double, and a reference implementation of the protocol):
    ```python
    def __init__(self) -> None:
        self._sequencer = EventSequencer()
        self.frames: list[SequencedEvent] = []
    async def emit(self, event: DebateEvent) -> None:
        self.frames.append(self._sequencer.stamp(event))
    @property
    def events(self) -> list[DebateEvent]: return [f.event for f in self.frames]
    @property
    def types(self) -> list[str]: return [f.event.type for f in self.frames]
    def encoded(self) -> str: return "".join(encode_sse(f) for f in self.frames)
    ```
- **PATTERN**: service module style in `backend/app/services/llm.py` (docstring, small classes).
- **IMPORTS**: `from typing import Protocol, runtime_checkable`;
  `from app.schemas.events import DebateEvent, SequencedEvent`.
- **GOTCHA**:
  - Use `"\n"` separators, never `os.linesep`.
  - Do **not** `json.dumps(event.model_dump())`. `model_dump()` leaves enums as Enum objects, and
    stdlib json differs (it `\u`-escapes non-ASCII by default). Use `model_dump_json()` only, so
    that the wire and `parse_event` are symmetrical.
  - `runtime_checkable` only checks that `emit` exists, not that it is async. That is fine for the
    test.
  - Keep the module free of FastAPI/`StreamingResponse` imports. The endpoint is B-T4.
- **VALIDATE**:
  `cd backend && python -c "from app.services.events import EventSequencer, encode_sse; from app.schemas.events import RoundCompletedEvent as R; print(repr(encode_sse(EventSequencer().stamp(R(round=1)))))"`
  should print `'id: 1\nevent: round_completed\ndata: {"type":"round_completed","round":1}\n\n'`.

### CREATE `frontend/src/types/debateEvents.ts`

- **IMPLEMENT** (types only, all exported, with a header comment citing KAN-17, DEC-003/004 and
  saying it mirrors `backend/app/schemas/events.py`):
  ```ts
  /** Mirrors backend/app/schemas/events.py (KAN-17). Server → client only (DEC-004). */
  export type Archetype = "advocate" | "skeptic" | "pragmatist";
  export type TurnStatus = "ok" | "skipped";
  export type DebateStatus = "pending" | "running" | "completed" | "failed";

  /** Mirrors PersonaOut. UI color is derived from `archetype` (DEC-006), not sent. */
  export interface PersonaOut { id: string; archetype: Archetype; name: string; stance: string; }
  export interface Case { option: string; argument: string; }

  export interface PersonasAssignedEvent { type: "personas_assigned"; debate_id: string; personas: PersonaOut[]; }
  export interface TurnStartedEvent { type: "turn_started"; round: number; persona_id: string; }
  export interface TurnDeltaEvent { type: "turn_delta"; round: number; persona_id: string; text: string; }
  export interface TurnCompletedEvent { type: "turn_completed"; round: number; persona_id: string; turn_id: string; status: TurnStatus; content: string; }
  export interface RoundCompletedEvent { type: "round_completed"; round: number; }
  export interface VerdictEvent { type: "verdict"; recommendation: string; cases: Case[]; tradeoffs: string[]; }
  export interface DoneEvent { type: "done"; debate_id: string; status: DebateStatus; }
  export interface ErrorEvent { type: "error"; code: string; message: string; }

  export type DebateEvent =
    | PersonasAssignedEvent | TurnStartedEvent | TurnDeltaEvent | TurnCompletedEvent
    | RoundCompletedEvent | VerdictEvent | DoneEvent | ErrorEvent;
  export type DebateEventType = DebateEvent["type"];
  export type DebateEventOf<T extends DebateEventType> = Extract<DebateEvent, { type: T }>;
  export type TerminalDebateEvent = DoneEvent | ErrorEvent;

  /** A parsed SSE frame: `seq` is the frame's `id:` (MessageEvent.lastEventId), not part of data. */
  export interface SequencedDebateEvent { seq: number; event: DebateEvent; }
  ```
  Format each interface multi-line, like `chat.ts` (the one-liners above are only to keep the
  plan compact).
- **PATTERN**: `frontend/src/types/chat.ts` (exported interfaces, string-literal unions).
- **GOTCHA**:
  - **No runtime values** (no `const` arrays or enums). The ticket says types only, and
    `isolatedModules` + TS `enum` would emit code.
  - Every declaration must be `export`ed, or `noUnusedLocals` fails `tsc`.
  - Field names stay `snake_case` to match the JSON on the wire. Do not camelCase them.
  - Keep each `type: "<name>"` literal on the **same line** as `type:` (the Python drift-guard
    test greps `type: "<name>"`).
- **VALIDATE**: `cd frontend && npx tsc -b` (see the Validation Level 1 prerequisites for
  `npm install`).

### CREATE `backend/tests/test_events.py`

- **IMPLEMENT**. Build a module-level `SAMPLES: list[DebateEvent]` with one instance per type in
  `EVENT_TYPES` order, using realistic values: `PersonasAssignedEvent` with 3 `PersonaOut`s (one
  per `Archetype`), a `TurnCompletedEvent` with `status=TurnStatus.SKIPPED, content=""`, a
  `VerdictEvent` from `Verdict(...).model_dump()`, a `DoneEvent(status=DebateStatus.COMPLETED)`,
  and `ErrorEvent(code="judge_failed", message="…")`. Add a helper
  `_frame_lines(frame) -> list[str]` that returns `frame.split("\n")`. Tests:
  1. `test_samples_cover_every_event_type`: `[e.type for e in SAMPLES] == list(EVENT_TYPES)` and
     `len(EVENT_TYPES) == 8`.
  2. `test_event_set_is_exactly_the_outbound_contract` (DEC-004): `set(EVENT_TYPES)` equals the
     literal 8-name set from the spec, and the discriminator tags of the `DebateEvent` union equal
     it too. Derive them from
     `typing.get_args(typing.get_args(DebateEvent)[0])` → each model's
     `model_fields["type"].default`, which proves there are no hidden or inbound variants.
  3. `@parametrize("event", SAMPLES, ids=EVENT_TYPES)`
     `test_encode_sse_frame_format(event)`: with `frame = encode_sse(SequencedEvent(seq=7,
     event=event))`, assert `frame.endswith("\n\n")`, assert `"\r" not in frame`, and assert
     `_frame_lines(frame) == ["id: 7", f"event: {event.type}", f"data: {event.model_dump_json()}",
     "", ""]`, so there is exactly one `data:` line.
  4. `@parametrize` `test_data_round_trips_to_model(event)`: take the `data:` line of the frame,
     strip the `"data: "` prefix, and assert `parse_event(payload) == event` and
     `type(parse_event(payload)) is type(event)`.
  5. `test_turn_delta_multiline_and_unicode_survive`: use
     `text = "line one\nline two\r\n\ttabbed — “quotes” ünïcödé 🎉 中文   end"`. Encode it and
     assert the frame still has exactly 5 `"\n"`-split parts. Assert the data line contains
     `\\n` (escaped) and raw `🎉`. Assert that the parsed `.text == text`, exact including `\r\n`.
  6. `test_unknown_type_rejected`: `parse_event('{"type":"user_reply","text":"hi"}')` raises
     `ValidationError`. This is also the DEC-004 example of an inbound-looking type.
  7. `test_missing_type_rejected`: `parse_event('{"round":1}')` raises `ValidationError`.
  8. `test_extra_field_rejected`:
     `parse_event('{"type":"round_completed","round":1,"seq":1}')` raises. This also proves `seq`
     is not a payload field.
  9. `test_invalid_payload_rejected` (parametrized):
     - `{"type":"turn_completed", …, "status":"maybe"}` (bad enum)
     - `{"type":"turn_started","round":0,"persona_id":"p"}` (`ge=1`)
     - `{"type":"verdict","recommendation":"x","cases":[],"tradeoffs":["t"]}` (`min_length`)
  10. `test_events_are_frozen`: assigning `event.round = 2` raises `ValidationError`.
  11. `test_sequencer_is_monotonic_from_one`: stamping 5 events gives `seq == [1,2,3,4,5]`, and
      `.last == 5`. `EventSequencer(start=10).stamp(e).seq == 11`. `EventSequencer(start=-1)`
      raises `ValueError`. `SequencedEvent(seq=0, event=e)` raises `ValidationError`.
  12. `test_sequencers_are_independent_per_debate`: two sequencers interleaved each yield 1,2,3.
  13. `test_in_memory_sink_records_in_order_with_seq`: create `sink = InMemoryEventSink()`, assert
      `isinstance(sink, EventSink)`, then `asyncio.run(_emit_all(sink, SAMPLES))`. Assert
      `sink.events == SAMPLES`, `[f.seq for f in sink.frames] == list(range(1, 9))`, and
      `sink.types == list(EVENT_TYPES)`. Assert that `sink.encoded()` split on `"\n\n"` gives 8
      non-empty frames, with `id: 1` … `id: 8`.
  14. `test_personas_assigned_accepts_orm_rows(session)`: create a real debate + 3 personas via
      `repo.create_debate` / `repo.add_persona` (see `tests/test_repository.py` helpers). Assert
      that `PersonasAssignedEvent(debate_id=d.id, personas=d.personas)` validates and round-trips.
      This is the seam B-T3/B-T4 rely on.
  15. `test_verdict_event_from_verdict_schema`: `VerdictEvent(**Verdict(...).model_dump())`
      round-trips through `encode_sse` → `parse_event`.
  16. `test_typescript_mirror_declares_every_event_type` (drift guard):
      `ts = (Path(__file__).resolve().parents[2] / "frontend/src/types/debateEvents.ts").read_text()`.
      For each `t` in `EVENT_TYPES`, assert `f'type: "{t}"' in ts`.
- **PATTERN**: `backend/tests/test_orchestrator.py` (plain functions, `asyncio.run`),
  `backend/tests/test_repository.py` (building a debate + personas with the `session` fixture
  from `conftest.py`).
- **IMPORTS**: `import asyncio`, `from pathlib import Path`, `import typing`, `import pytest`,
  `from pydantic import ValidationError`, the `app.models.enums` enums,
  `from app.repositories import debates as repo`, `from app.schemas.debate import PersonaOut`,
  `from app.schemas.verdict import Case, Verdict`, everything needed from `app.schemas.events`,
  and `EventSequencer, EventSink, InMemoryEventSink, encode_sse` from `app.services.events`.
- **GOTCHA**:
  - **Split frames with `.split("\n")`, NEVER `.splitlines()`.** `str.splitlines()` also splits on
    ` `, `\x1c`–`\x1e`, `\x85` and similar, which Pydantic leaves raw in the JSON. That would
    make the unicode test fail spuriously, even though the frame is SSE-correct (SSE only splits
    on CR/LF).
  - `pytest-asyncio` is not installed, so drive async code with `asyncio.run`.
  - Check the exact `repo.add_persona` signature in `backend/app/repositories/debates.py` before
    using it (the orchestrator calls
    `repo.add_persona(session, debate.id, ModelArchetype(...), name, stance)`, at
    `services/orchestrator.py:110`). Reload with `repo.get_debate` so `.personas` is populated.
- **VALIDATE**: `cd backend && python -m pytest tests/test_events.py -q`

---

## TESTING STRATEGY

### Unit Tests
- The encoding matrix: every one of the 8 types × frame grammar (id/event/data/blank-line, LF-only,
  single data line).
- The round-trip: frame `data` → `parse_event` → an equal, same-typed model.
- Rejection: unknown/missing `type`, extra fields (including a `seq` smuggled into the payload),
  bad enum, `round < 1`, empty `cases`, and mutating a frozen event.
- Sequencing: monotonic from 1, seeding, independent per debate, `seq >= 1` enforced.
- Sink: satisfies the `EventSink` protocol, keeps order, and stamps seq 1..n.

### Integration Tests
- `PersonasAssignedEvent` built directly from ORM `Persona` rows via the `session` fixture. This is
  the only DB touch-point, and it proves the B-T3/B-T4 seam.
- The Python ↔ TypeScript drift guard (a text check of `debateEvents.ts`) plus `tsc`.
- End-to-end streaming (orchestrator → sink → HTTP) belongs to B-T3/B-T4 and is **not** in this
  ticket.

### Edge Cases
- Multi-line `turn_delta` text with `\n`, `\r\n`, `\t`, and U+2028: it stays one data line and
  round-trips exactly.
- Non-ASCII/emoji/CJK are emitted raw UTF-8 and parse back identically.
- An empty `turn_delta.text` is accepted (so the hot path never crashes).
- `turn_completed` for a skipped turn has `content == ""` and `status == "skipped"`.
- `seq` smuggled into `data` is rejected. `seq=0` in the envelope is rejected.

### E2E / Browser Automation
**N/A for this ticket.** It adds no UI, route, or runtime consumer. The TS file is types only
(DEC-010 gate not applicable), and the backend change is headless. Browser validation of the
stream arrives with EPIC-C, and `curl -N` validation with B-T4.

---

## VALIDATION COMMANDS

Run from the worktree root unless noted. Prefer the project's `/validate` skill, which wraps these.

**Environment prerequisites (verified while planning):**
- The worktree has **no** `backend/.venv`. The `python`/`pytest`/`ruff` on PATH resolve to the main
  checkout's venv (`/Users/oleh_saveliev/Desktop/Projects/AI-Delivery-Flow/backend/.venv`, pydantic
  2.13.4). That is fine, because pytest uses `pythonpath = ["."]`, so running from the worktree's
  `backend/` imports the worktree's `app`. If it is not on PATH, activate that venv or
  `pip install -e 'backend[dev]'` into a fresh one.
- `frontend/node_modules` does **not** exist, in the worktree or the main checkout, and there is no
  `package-lock.json`. Run `npm install` first. It creates `frontend/package-lock.json`, so **do
  not commit it** as part of this ticket unless the team decides to (it is out of scope; see Open
  Questions). `node_modules/` is already gitignored.
- `npm run lint` references `eslint`, which is **not** a declared dependency, so it will fail. Do
  not use it as a gate.

### Level 1: Syntax & Style
```bash
cd backend && ruff check . && ruff format --check .
cd frontend && npm install && npx tsc -b
```

### Level 2: Unit Tests
```bash
cd backend && python -m pytest tests/test_events.py -q
```

### Level 3: Full Backend Suite (no regressions)
```bash
cd backend && python -m pytest -q
```

### Level 4: Manual Validation
```bash
cd backend && python -c "
import asyncio
from app.schemas.events import TurnDeltaEvent, DoneEvent, parse_event
from app.models.enums import DebateStatus
from app.services.events import InMemoryEventSink
s = InMemoryEventSink()
async def go():
    await s.emit(TurnDeltaEvent(round=1, persona_id='p1', text='Hello\nwörld 🎉'))
    await s.emit(DoneEvent(debate_id='d1', status=DebateStatus.COMPLETED))
asyncio.run(go())
print(s.encoded(), end='')
data = s.encoded().split('\n')[2].removeprefix('data: ')
print('round-trip ok:', parse_event(data) == s.events[0])
"
```
Expected output: two frames, `id: 1` and `id: 2`, each followed by a blank line, and
`round-trip ok: True`.

Optional full build: `cd frontend && npm run build` (runs `tsc -b && vite build`). This should
also pass, but `tsc -b` is the gate for this ticket.

### Level 5: E2E / Browser Automation
**N/A.** There is no UI or route in this ticket (see Testing Strategy).

### Level 6: Additional Validation (Optional)
```bash
# Confirm there is no pytest-asyncio dependency creep and no new runtime deps:
git diff --stat -- backend/pyproject.toml frontend/package.json   # expect: no changes
```

---

## ACCEPTANCE CRITERIA (from KAN-17)

- [ ] Pydantic models for all 8 events (`personas_assigned`, `turn_started`, `turn_delta`,
      `turn_completed`, `round_completed`, `verdict`, `done`, `error`), discriminated by `type`,
      with unknown types rejected (`parse_event` → `ValidationError`).
- [ ] `encode_sse` emits spec-compliant frames: `id: <seq>`, `event: <type>`, single-line JSON
      `data`, and a blank-line terminator. `seq` is monotonically increasing per debate
      (`EventSequencer`, starting at 1).
- [ ] Multi-line/unicode text in `turn_delta` survives the JSON round-trip.
- [ ] `EventSink` protocol (async `emit(event)`) + `InMemoryEventSink` for tests.
- [ ] Matching TypeScript types in `frontend/src/types/debateEvents.ts`, types only.
- [ ] Unit tests encode each event type (including newline/unicode) and round-trip `data` → model.
      `tsc -b` passes.
- [ ] DEC-003 honored (`round_completed{round}`, emitted twice by B-T3) and DEC-004 honored
      (server → client only; test-asserted exact outbound set).
- [ ] `ruff check` / `ruff format --check` clean. The full `pytest` suite is green with no
      regressions.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order, each task's `VALIDATE` passed
- [ ] Levels 1–4 pass (ruff, tsc, test_events, full suite, manual frame print)
- [ ] `frontend/package-lock.json` (created by `npm install`) **not** staged unless decided
      otherwise
- [ ] Ready for `/code-review` → `/commit`. The commit:
  - carries the trailer `Decisions: DEC-003, DEC-004` (the change touches `backend/app/**` and
    `frontend/src/**`, and the commit-msg hook requires it)
  - updates `docs/architecture.md` per DEC-011: add `app/schemas/events.py` and
    `app/services/events.py` rows to the Backend table (✅ KAN-17), add `events` to the
    `app/schemas/*.py` row, add a short "Stream event contract (KAN-17 — DEC-003/004)"
    subsection with the event table + frame format + the envelope/seq rule, add
    `src/types/debateEvents.ts` under Frontend, and add a dated Change Log row. Mirror the same to
    Confluence 917506.
  - updates the Decision Log **Implementation Tracker / "Implemented by"** for DEC-004
    (currently "—") with `KAN-17 · <sha> (stream contract: server→client only)`, and appends
    `KAN-17 · <sha> (round_completed ×2 contract)` to DEC-003

---

## NOTES

- **Out of scope (don't build):**
  - orchestrator emission and the thread→loop delta bridge (KAN-19 / B-T3)
  - `LLMService.stream_turn` and `config.turn_timeout_seconds` (KAN-18 / B-T2, planned in parallel;
    **do not touch `services/llm.py` or `core/config.py`**)
  - the broker, replay, `/stream` endpoint, keepalive `: ping`, and `retry:` field (B-T4/B-T5,
    blocked on Proposed DEC-012)
  - any UI or EventSource hook (EPIC-C)
- **Why no new DEC.** The epic spec froze the event names and payloads under DEC-003/004. The
  choices here (envelope-held `seq`, seq starts at 1, `data` without `seq`, `PersonaOut` as the
  persona payload, strict `extra="forbid"`, open-string error `code`) are wire-level details of
  that frozen contract and contradict no Accepted DEC. If the reviewer considers the `seq`/replay
  semantics architectural, fold them into DEC-012 (still Proposed, and it already covers
  "seq-ordered, no gaps or duplicates") rather than opening a separate DEC.
- **B-T3 ergonomics this enables:** `await sink.emit(PersonasAssignedEvent(debate_id=d.id,
  personas=d.personas))` straight from ORM rows, and
  `TurnCompletedEvent(round=r, persona_id=pid, turn_id=row.id, status=row.status,
  content=row.content)` after commit (DEC-008).
- **B-T4 ergonomics this enables:** the broker owns one `EventSequencer` per debate, keeps a
  `list[SequencedEvent]` history for replay, and writes `encode_sse(item)` into a
  `StreamingResponse(media_type="text/event-stream")`. Use `EventSequencer(start=n)` if replay
  re-stamps.
- **Client note (EPIC-C):** get `seq` from `MessageEvent.lastEventId` (a string, so
  `Number(...)` it), register one `addEventListener` per `DebateEventType`, and derive the persona
  color from `archetype`.

**Confidence score: 9/10** for one-pass success. The risks are tooling only: `npm install` needs
network access, and the tests depend on the venv path on PATH.
