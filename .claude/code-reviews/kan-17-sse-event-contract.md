# Code Review: KAN-17 (EPIC-B · B-T1) — SSE event contract & wire encoding

Branch `KAN-17` (worktree `agent-ac07d424b0e80acd9`), uncommitted vs `main` @ `ef1ff7d`.
Reviewed against: Jira KAN-17, `docs/specs/epic-b-live-streaming.md` (B-T1), Decision Log
(Confluence 1015810, v11), `docs/architecture.md`, and the author's plan
`.claude/plans/kan-17-sse-event-contract.md` (treated as intent, not ground truth).

**Stats:**

- Files Modified: 0
- Files Added: 6 (5 intended + 1 stray `frontend/package-lock.json`)
- Files Deleted: 0
- New lines: 3191 (1308 without the 1883-line lockfile; of which 594 are source/test and 714 the plan)
- Deleted lines: 0

New files:
`backend/app/schemas/events.py` (151), `backend/app/services/events.py` (84),
`backend/tests/test_events.py` (252), `frontend/src/types/debateEvents.ts` (107),
`.claude/plans/kan-17-sse-event-contract.md` (714), `frontend/package-lock.json` (1883, untracked by-product of `npm install`).

## Verification run

| Gate | Result |
| --- | --- |
| `pytest` (backend, full suite) | 93 passed (incl. all new `test_events.py` cases) |
| `ruff check` on the 3 new Python files | All checks passed |
| `ruff check .` (whole backend) | 3 errors, all in files untouched by this change (pre-existing on `main`, see finding 9) |
| `ruff format --check .` | 46 files already formatted |
| `npx tsc -b` (frontend) | exit 0 |

Probes (scratchpad script, not in repo) confirmed: nested models are mutable inside "frozen"
events, `hash()` of a frozen event with a list field raises `TypeError`, nested extra keys are
silently accepted by `parse_event`, `DoneEvent(status="pending")` is accepted, and U+2028 /
U+0085 / NUL in `turn_delta` text never break the single-line `data:` (only CR/LF matter to
SSE, and Pydantic escapes those).

## Acceptance criteria (KAN-17 / spec B-T1)

| Criterion | Status |
| --- | --- |
| Pydantic models for all 8 events, discriminated by `type`; unknown types rejected | Met (`test_unknown_type_rejected`, `test_missing_type_rejected`, union-tag test) |
| `encode_sse` emits `id:` / `event:` / single-line JSON `data:` / blank-line terminator; per-debate monotonic `seq` | Met. `encode_sse` takes a `SequencedEvent`, not a bare event, and `EventSequencer` assigns `seq`. That is a reasonable reading of `encode_sse(event)` |
| Multi-line / unicode `turn_delta` survives the round-trip | Met (`test_turn_delta_multiline_and_unicode_survive`) |
| `EventSink` protocol (async `emit`) + in-memory list sink | Met |
| Matching TS types in `frontend/src/types/debateEvents.ts`, `tsc` passes | Met. Field-by-field check done by hand, all 8 interfaces and 3 enums match |

## Decision Log check

- DEC-003 (2 rounds): honored. `round >= 1`, and the round count stays in the guardrails and orchestrator. Not hardcoding `<= 2` is a sound choice.
- DEC-004 (watch-only): honored. Outbound-only union, `parse_event` documented as not an input surface, and there is a test for it.
- DEC-007 (non-streamed judge): honored. There is one `verdict` event, which reuses `Case`/`Tradeoff`.
- DEC-008 (persist before announce): honored at the contract level. `turn_completed` carries `turn_id` and the authoritative `content`.
- DEC-005: the in-process, non-thread-safe sequencer fits it.
- Proposed DEC-012: no contradiction. `KNOWN_ERROR_CODES` anticipates its codes but only as information (finding 8).
- No Accepted DEC is contradicted. No new, unlogged architectural decision was introduced.

## Compatibility with KAN-18 (sibling worktree `agent-a7fae55505bd227ed`, read-only)

The two changes share no files. KAN-18 touches `llm.py`, `config.py` and `test_llm.py`. KAN-18's
`stream_turn(on_delta: Callable[[str], None], ...)` calls `on_delta` synchronously on the worker
thread. This contract's `EventSink.emit` is async, and `EventSequencer` is documented as
loop-thread-only, so B-T3 must bridge with `call_soon_threadsafe` or a queue. Both sides say so,
and they are compatible. One semantic gap sits where they meet (finding 7).

## Findings

```
severity: medium
file: frontend/package-lock.json
line: 1
issue: Stray, out-of-scope 1883-line lockfile would be swept into the KAN-17 commit
detail: The file is an untracked by-product of running `npm install` to get `tsc`. It was introduced
  by this change: `main` has no lockfile. The `/commit` skill commits "all uncommitted changes", so
  the file would land in a contract ticket without anyone deciding it should. Adopting a lockfile
  is a repo-wide tooling choice. The author's own plan (line 669) says it should "not be staged
  unless decided".
suggestion: Delete it (or leave it unstaged) before committing KAN-17. If the team wants a
  lockfile, which is good practice, add it in a separate `chore(frontend)` commit.
```

```
severity: medium
file: docs/architecture.md
line: 33
issue: Architecture doc is stale after this change (DEC-011); Confluence mirror 917506 must follow
detail: Line 33 says "no streaming ... yet". The `app/schemas/*.py` row (line 53) lists only
  `chat, persona, verdict, debate`. There is no row for `app/services/events.py`
  (EventSequencer / encode_sse / EventSink / InMemoryEventSink). The Frontend section does not
  mention `src/types/debateEvents.ts`. The "Decisions realized" table and the Change Log have no
  KAN-17 entry. This change creates the frozen stream contract that B-T3/B-T4/EPIC-C build on,
  which is exactly the kind of change DEC-011 requires the doc to record.
suggestion: At commit time: add the `schemas/events.py` and `services/events.py` rows, a short
  "Stream event contract" subsection (8 events, frame format, seq ownership, terminal rule),
  the TS mirror, and a Change Log row "KAN-17 · DEC-003/004". Then update Confluence 917506 in
  lockstep. Use the commit trailer `Decisions: DEC-003, DEC-004`, and update the Decision Log
  "Implemented by" for DEC-004 (currently "—") to note KAN-17 as a partial realization, contract
  only.
```

```
severity: low
file: backend/app/schemas/events.py
line: 32
issue: "Strict and immutable" holds only at the top level
detail: `frozen=True, extra="forbid"` covers the event model only. Nested `PersonaOut` and `Case`
  are neither frozen nor extra-forbidding, and list fields are mutable. Probe results:
  `e.personas[0].name = "X"` and `e.personas.append(...)` both succeed on a "frozen" event.
  `parse_event` silently drops an unknown nested key such as `personas[0].color`. `hash(event)`
  raises `TypeError` for any event with a list field, although frozen Pydantic models otherwise
  advertise hashability. The docstring promise ("immutable once emitted", "no unknown fields")
  is therefore stronger than the behavior. This was introduced by this change, though it
  inherits from the existing `PersonaOut`/`Case`.
suggestion: Either use `tuple[PersonaOut, ...]` / `tuple[Case, ...]` / `tuple[Tradeoff, ...]` and
  event-local frozen, extra-forbid nested models, or reword the docstring to "top-level fields
  are immutable and strict". The tuple option keeps the JSON wire format unchanged.
```

```
severity: low
file: backend/app/schemas/events.py
line: 92
issue: `DoneEvent.status` accepts any DebateStatus, though `done` is defined as "terminal: success"
detail: `{"type":"done","status":"pending"}` (or "running"/"failed") validates. The spec defines
  `done` as terminal success, and Proposed DEC-012 routes FAILED to a terminal `error`. So the
  only meaningful value is `completed`. Leaving it open weakens the frozen contract, and a
  producer bug such as emitting `done` while still `running` would pass validation. This is a
  plan choice (plan line 252) introduced here.
suggestion: Narrow to `status: Literal[DebateStatus.COMPLETED]` (TS: `status: "completed"`), or
  document explicitly why other statuses are allowed. Revisit if the accepted DEC-012 differs.
```

```
severity: low
file: backend/tests/test_events.py
line: 249
issue: Python<->TS "drift guard" only checks the 8 `type:` literals, not fields or enum values
detail: Renaming a Python field (e.g. `text` -> `chunk`), adding a required field, or adding an
  enum member (e.g. a new DebateStatus) passes every test and `tsc`, and the TS mirror silently
  goes stale. The plan presents this test as the drift guard (plan lines 46, 559), but it
  protects only the discriminator. It also doesn't catch an extra TS-only (e.g. inbound) variant,
  which matters under DEC-004. This is test weakness introduced here. The mirror is currently
  correct: verified by hand.
suggestion: For each model, extract the TS interface body with a regex keyed on
  `interface <Name>Event {` and assert its field names equal `model_fields`. Also assert that the
  string-literal sets of `Archetype`/`TurnStatus`/`DebateStatus` equal the Python enums, and that
  the `DebateEvent` union members are exactly the 8 interfaces.
```

```
severity: low
file: backend/app/schemas/events.py
line: 60
issue: Contract does not say that `turn_completed.content` replaces streamed deltas (KAN-18 interplay)
detail: KAN-18's `stream_turn` can emit deltas and then fail mid-stream, returning `skipped` with
  `text == ""` ("consumers must discard partial text"). On the wire that becomes some
  `turn_delta`s followed by `turn_completed{status: skipped, content: ""}`. The contract calls
  `content` "authoritative" but never says that clients must replace the text accumulated from
  deltas, which means clearing it for a skipped turn. An EPIC-C client that appends deltas and
  treats `turn_completed` as a marker only would show orphaned partial text.
suggestion: State it in the `TurnCompletedEvent` docstring and the TS comment
  (`debateEvents.ts:49`): "Clients MUST replace any text accumulated from `turn_delta` for this
  (round, persona_id) with `content`; for `status: skipped`, discard it."
```

```
severity: low
file: backend/app/schemas/events.py
line: 133
issue: `KNOWN_ERROR_CODES` presumes the outcome of Proposed (not Accepted) DEC-012, and a test pins it
detail: The codes `judge_failed`/`timeout`/`internal` come from DEC-012's proposal and B-T4/B-T5,
  which are blocked. The constant is informational, but `test_event_set_is_exactly_the_outbound_contract`
  (test line 101) asserts it. That makes a pending decision look settled, and the test will need
  editing if DEC-012 is amended.
suggestion: Mark it provisional in the comment ("per Proposed DEC-012; finalize in B-T4") and drop
  the assertion from the contract test, or move it to B-T4.
```

```
severity: low
file: frontend/src/types/debateEvents.ts
line: 14
issue: Mis-cited decision: persona color is attributed to DEC-006
detail: DEC-006 is the threaded chat layout and says nothing about persona colors. Colors today
  live in `app/prompts/personas.py` (KAN-5) on the internal `schemas.persona.Persona`, and they
  are already absent from `PersonaOut` (KAN-9). A wrong DEC reference breaks the traceability
  chain CLAUDE.md requires.
suggestion: Drop the DEC reference, or cite the actual source ("color is derived from `archetype`
  client-side; `PersonaOut` omits it, KAN-9").
```

```
severity: low
file: backend/tests/test_debates_api.py
line: 10
issue: Pre-existing: `ruff check .` fails with 3 errors in untouched files (not introduced by KAN-17)
detail: RUF100 at test_debates_api.py:10, and C408 at test_guardrails.py:7 and test_llm.py:28.
  All three exist on `main`, and the new files pass ruff cleanly. They will still make the
  project-wide `/validate` lint gate red for this ticket.
suggestion: Fix them in a separate small `chore(tests)` commit (`ruff check --fix` plus two
  `dict()` -> literal rewrites), or note them as known-red in the PR. Do not fold them into KAN-17.
```

## Notes (no action needed)

- `encode_sse` correctly uses `model_dump_json()` rather than `json.dumps`: the wire and
  `parse_event` stay symmetric, and non-ASCII stays raw UTF-8. The `split("\n")` rather than
  `splitlines()` detail in the tests is correct for SSE.
- The `EventSink` producer/sink split, with sequencing owned by the sink, is clean, and so is
  `EventSequencer(start=...)` for broker replay continuation.
- A sequencer that is not thread-safe is acceptable and documented. B-T3 must bridge KAN-18's
  worker-thread `on_delta` onto the loop.

## Verdict

Ready after fixes. Remove the stray lockfile from the commit, and apply the architecture-doc and
Decision Log updates at commit time. The low findings are recommended but not blocking.
