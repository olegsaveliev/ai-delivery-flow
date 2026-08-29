# Feature: Debate Orchestrator — concurrent turns, sequential rounds (KAN-7 / TICKET-4)

The following plan should be complete, but it's important that you validate documentation and
codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right
files — in particular this feature straddles **two separate `Archetype` enums** (`app.schemas.persona.Archetype`
and `app.models.enums.Archetype`); see the GOTCHAs.

## Feature Description

The debate orchestrator is the engine at the heart of Second Opinion. Given a persisted `Debate`,
it assigns the three fixed personas, then runs a **2-round** debate in which the **three persona
turns within a round run concurrently** and **rounds run sequentially** — round N is given the
transcript of all rounds `< N` so personas answer each other rather than producing three
monologues. Every completed turn is persisted to SQLite as it lands; a turn that fails (or is
blocked by a guardrail) is recorded as `skipped` and the debate continues. The orchestrator
produces the full transcript the judge (TICKET-5) later consumes. There are **no HTTP concerns**
here — that's TICKET-6.

## User Story

As a viewer,
I want a 2-round debate where personas answer each other,
So that it feels like a real discussion rather than three monologues.

## Problem Statement

The building blocks exist in isolation — persona assignment (KAN-5), a guarded LLM turn client
(KAN-6), and a SQLite persistence layer (KAN-10) — but nothing drives the actual debate loop.
There is no component that sequences rounds, runs the per-round turns concurrently, threads the
growing transcript back into each round, persists turns durably, and tolerates a dropped turn
without aborting the whole debate.

## Solution Statement

Add `backend/app/services/orchestrator.py` with a single public coroutine `run_debate(session, debate, ...)`.
It:
1. Assigns personas via `assign_personas` (KAN-5) and persists them via the repository (KAN-10).
2. Runs `max_rounds` (2 — DEC-003) rounds **sequentially**. For each round it dispatches the three
   persona turns **concurrently** by wrapping the *synchronous* `LLMService.run_turn` (KAN-6) in
   `asyncio.to_thread` and awaiting them, persisting each turn to SQLite **as it completes** on the
   orchestrator's own (single) thread.
3. Threads the transcript of prior rounds into each persona's prompt.
4. Records failed/blocked turns as `skipped` and continues; marks the debate `COMPLETED` at the end.
5. Returns the `Debate` (with `turns` ordered) for the judge to consume.

**Why `asyncio.to_thread` and not a native async client:** KAN-6 shipped a *synchronous* `LLMService`
(blocking `Anthropic` client + `time.sleep` backoff). The acceptance criterion "turns run
concurrently (async)" is satisfied by making `run_debate` a coroutine and fanning the three blocking
turn calls out across a thread pool — the standard bridge for a sync SDK. This is an implementation
detail that **does not contradict any Accepted decision** and does not warrant a new DEC (DEC-003 is
about round count, not the concurrency mechanism). Do **not** rewrite `LLMService` to be async — that
would touch KAN-6's merged surface and is out of scope.

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: Medium
**Primary Systems Affected**: `backend/app/services` (new orchestrator), persistence layer (writes),
LLM service (consumer), personas service (consumer)
**Dependencies**: No new external libraries. Uses stdlib `asyncio` (`to_thread`, `as_completed`,
`create_task`, `gather`). All internal deps (KAN-5/6/10) are merged on `main`.

---

## GOVERNING DECISIONS (MANDATORY — per CLAUDE.md)

This task **implements / is constrained by**:

- **DEC-003** — Fixed **2 rounds**, no early-stop/convergence heuristic. Round count comes from
  `settings.max_rounds` (= 2). Do **not** add any convergence logic. **KAN-7 is the story that
  realizes DEC-003** — its "Implemented by" row is currently `—`.
- **DEC-001 / DEC-002** — three fixed archetypes with per-decision stances; reuse `assign_personas`,
  do not invent personas or change the set.
- **DEC-008** — persist each turn to SQLite as it lands (durability across the debate).
- **DEC-007** — personas run on the `personas` tier (Sonnet) — pass `tier="personas"` to `run_turn`.
- **DEC-005** — single-user/local; no ownership columns or auth anywhere.

**Commit requirement (enforced by `.githooks/commit-msg`):** the commit touching `backend/app/**`
must carry a trailer `Decisions: DEC-003` (optionally `DEC-001, DEC-002, DEC-007, DEC-008`).
**On completion**, update DEC-003's *Implemented by* entry and the Implementation Tracker row in the
Decision Log (Confluence page 1015810) with `KAN-7 · <commit>`.

**No new decision is introduced** by this plan. If during implementation you find you must contradict
an Accepted DEC (e.g. you believe rounds should early-stop), STOP and raise a Proposed `DEC-xxx`
instead of deviating silently.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — YOU MUST READ THESE BEFORE IMPLEMENTING

- `backend/app/services/llm.py` (lines 18–33, 65–171) — Why: `TurnResult` shape and the **exact
  `run_turn` signature** you call. Note it is **synchronous**, **keyword-only** (`*`), takes
  `tier`, `messages`, `guardrails`, `round_number` (1-indexed), `persona_index` (0-indexed),
  `max_tokens`, `system`, and **never raises** — it returns `TurnResult(status="ok"|"skipped")`.
  `get_llm_service()` is the lazy singleton accessor.
- `backend/app/services/personas.py` (lines 12–31) — Why: `assign_personas(decision, context)`
  returns `list[app.schemas.persona.Persona]` (Pydantic, in-memory, has `archetype`/`name`/`color`/`stance`,
  **no id/debate_id**).
- `backend/app/prompts/personas.py` (lines 30–92) — Why: `system_prompt_for(archetype)` gives each
  persona's system prompt (used to drive its turns — see the module docstring which explicitly points
  at TICKET-4). `ARCHETYPES` and `spec_for` available if needed.
- `backend/app/schemas/persona.py` (lines 6–27) — Why: the **schemas** `Archetype` enum + `Persona`
  Pydantic model returned by `assign_personas`.
- `backend/app/core/guardrails.py` (lines 23–71) — Why: `DebateGuardrails` (one per debate) and
  `guardrails_from_settings()` factory. `check` reads and `record_usage` mutates `tokens_spent`
  (relevant to the concurrency GOTCHA).
- `backend/app/repositories/debates.py` (lines 18–60) — Why: `create_debate`, `add_persona(session,
  debate_id, archetype, name, stance)`, `add_turn(session, debate_id, persona_id, round, content, status)`.
  These **flush but do not commit** — commit is the caller's responsibility. `add_persona`/`add_turn`
  take the **models** `Archetype`/`TurnStatus` enums.
- `backend/app/models/enums.py` (lines 4–24) — Why: the **models** `Archetype`, `TurnStatus`
  (`OK`/`SKIPPED`), `DebateStatus` (`PENDING`/`RUNNING`/`COMPLETED`/`FAILED`) enums used at the ORM layer.
- `backend/app/models/turn.py` (lines 12–35) — Why: `Turn` columns — `round`, `content` (non-null),
  `status`, `created_at` (default `datetime.utcnow`). `content` is NOT nullable → a skipped turn must
  persist `content=""`, not `None`.
- `backend/app/models/debate.py` (lines 11–44) — Why: `Debate` has `status` and relationships
  `personas`/`turns`/`verdict`. You will set `debate.status`.
- `backend/app/db/session.py` (lines 44–74) — Why: `get_session()` context manager (commit-on-exit)
  and `get_session_factory()`. The orchestrator accepts a `Session` so tests can inject the fixture.
- `backend/app/schemas/chat.py` (lines 4–6) — Why: `ChatMessage{role, content}` — the message type
  `run_turn` expects (`list[ChatMessage]`).
- `backend/tests/conftest.py` (lines 22–47) — Why: the `session` fixture (shared in-memory SQLite via
  `StaticPool`, FKs ON). Your orchestrator tests use it directly.
- `backend/tests/test_llm.py` (lines 11–43) — Why: the **injection + stub pattern** to mirror
  (`SimpleNamespace` responses, injecting a fake client / `sleep`). Your orchestrator tests inject a
  stub `llm` the same way.
- `backend/tests/test_repository.py` (lines 10–37, 130–145) — Why: how a full debate is built in a
  test (`create_debate` → `add_persona` ×3 → `add_turn` ×6) and how a skipped turn is asserted.

### New Files to Create

- `backend/app/services/orchestrator.py` — the `run_debate` coroutine + private helpers.
- `backend/tests/test_orchestrator.py` — unit tests (all-OK debate, transcript threading, forced skip).

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [Python `asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
  - Section: running blocking code in a thread. Why: bridges the synchronous `run_turn` into the async
    orchestrator so the three per-round turns run concurrently.
- [Python `asyncio.as_completed`](https://docs.python.org/3/library/asyncio-task.html#asyncio.as_completed)
  - Section: iterating awaitables as they finish. Why: lets you persist each turn **as it lands**
    (per the acceptance criterion) rather than waiting for the whole round.
- [SQLAlchemy Session thread-safety](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe)
  - Section: "the Session is not safe for use in concurrent threads." Why: this is exactly why all
    DB writes stay on the orchestrator's own thread while only the LLM calls fan out to threads.

### Patterns to Follow

**Dependency injection for testability** (mirror `LLMService.__init__(sleep=...)` and `test_llm.make_service`):
`run_debate` takes optional `llm` and `guardrails` params defaulting to `get_llm_service()` /
`guardrails_from_settings()`, so tests inject a stub with a `run_turn(...) -> TurnResult` method and
never hit the network.

**Repository usage** (mirror `test_repository._build_full_debate`): call `repo.add_persona` /
`repo.add_turn` with the **models** enums; the orchestrator owns commits (`session.commit()`).

**Module docstring + DEC references in comments** (mirror `guardrails.py` / `personas.py` headers):
open the module with a docstring naming the governing DECs and the sync→async bridge rationale.

**Keyword-only service calls** (mirror `run_turn`'s `*` signature): call `llm.run_turn(tier=...,
messages=..., guardrails=..., round_number=..., persona_index=..., system=...)` with keywords.

**Logging** (mirror `llm.py` `logger = logging.getLogger(__name__)`): use a module logger; log
round/debate lifecycle at `info`, skips at `warning`.

**Line length 100, ruff-clean, target py311** (`pyproject.toml`). No mypy is configured; ruff is the gate.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation

Define the module skeleton, imports, logger, and the private prompt/transcript helpers so the main
loop reads cleanly.

**Tasks:**
- Create `orchestrator.py` with docstring (DEC-003/001/002/008/007 + sync→async rationale), imports,
  and module logger.
- Implement `_build_turn_messages(...)` that assembles a single `ChatMessage(role="user", ...)`
  containing the decision + context, this persona's stance, and the prior-rounds transcript.

### Phase 2: Core Implementation

Implement `run_debate` — persona assignment/persistence, the sequential-round / concurrent-turn loop,
persist-as-it-lands, skip tolerance, and status transitions.

**Tasks:**
- Assign personas (`assign_personas`) and persist them (`repo.add_persona`), pairing each ORM row with
  its schema persona (for `system_prompt_for` + stance).
- Set `debate.status = RUNNING`; commit.
- Loop rounds `1..max_rounds`; per round fan out three `asyncio.to_thread(llm.run_turn, ...)` tasks,
  persist each via `repo.add_turn` + `session.commit()` as it completes, accumulate OK turns into the
  in-memory transcript for the next round.
- Set `debate.status = COMPLETED`; commit; return the debate.

### Phase 3: Integration

No wiring into HTTP here (that's TICKET-6). Integration is limited to the service layer contracts:
`run_debate` returns a `Debate` whose `turns` are the transcript the judge (TICKET-5) will consume.
Confirm the public name/signature is import-friendly for T5/T6.

**Tasks:**
- Export `run_debate` at module top level (no `__init__.py` change required — services are imported by
  full path, mirroring `from app.services.llm import ...`).

### Phase 4: Testing & Validation

Unit-test the loop with an injected stub LLM (no network), covering the happy path, transcript
threading, and forced-skip resilience.

**Tasks:**
- Add `test_orchestrator.py` with a `StubLLM`, using the `session` fixture and `asyncio.run`.
- Run ruff + the full pytest suite; confirm zero regressions.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom.

### CREATE `backend/app/services/orchestrator.py` — module skeleton & helper

- **IMPLEMENT**: Module docstring naming governing DECs (003 rounds, 001/002 personas, 008 persist,
  007 tier) and the "synchronous `run_turn` fanned out via `asyncio.to_thread`; DB writes stay on the
  orchestrator thread because a `Session` is not thread-safe" rationale. Add `logger = logging.getLogger(__name__)`.
  Then implement:
  ```python
  def _build_turn_messages(
      *,
      decision: str,
      context: str | None,
      stance: str,
      transcript: list["_TurnRecord"],
      round_number: int,
  ) -> list[ChatMessage]:
      ...
  ```
  where `_TurnRecord` is a small dataclass/namedtuple `(name, archetype_value, round, content)`.
  Build ONE user message: the decision (+context if present), a line stating this persona's stance,
  the prior transcript formatted as `f"[Round {r}] {name}: {content}"` (empty string / "no prior
  discussion" note when `transcript` is empty), and a closing instruction to respond for this round
  engaging with the others.
- **PATTERN**: `ChatMessage` from `app.schemas.chat` (schemas/chat.py:4). System prompt is passed
  separately via `run_turn(system=...)`, NOT inside messages.
- **IMPORTS**:
  ```python
  import asyncio
  import logging
  from dataclasses import dataclass

  from sqlalchemy.orm import Session

  from app.core.guardrails import DebateGuardrails, guardrails_from_settings
  from app.models.debate import Debate
  from app.models.enums import Archetype as ModelArchetype
  from app.models.enums import DebateStatus, TurnStatus
  from app.prompts.personas import system_prompt_for
  from app.repositories import debates as repo
  from app.schemas.chat import ChatMessage
  from app.services.llm import LLMService, TurnResult, get_llm_service
  from app.services.personas import assign_personas
  ```
- **GOTCHA**: `run_turn` expects `list[ChatMessage]` — do not pass raw dicts. Keep the whole prompt in
  a single `user` message; the Anthropic API requires the first message to be `user`.
- **VALIDATE**: `cd backend && .venv/bin/ruff check app/services/orchestrator.py`

### ADD `run_debate` coroutine to `backend/app/services/orchestrator.py`

- **IMPLEMENT**:
  ```python
  async def run_debate(
      session: Session,
      debate: Debate,
      *,
      llm: LLMService | None = None,
      guardrails: DebateGuardrails | None = None,
      max_tokens_per_turn: int = 1024,
  ) -> Debate:
  ```
  Steps:
  1. `llm = llm or get_llm_service()`; `guardrails = guardrails or guardrails_from_settings()`.
  2. `settings = get_settings()`; read `max_rounds` (DEC-003). Import `get_settings` from
     `app.core.config`. (Add to imports.)
  3. Assign + persist personas:
     ```python
     assigned = assign_personas(debate.decision, debate.context)
     persona_pairs = []  # (orm_persona, schema_persona)
     for p in assigned:
         row = repo.add_persona(
             session, debate.id, ModelArchetype(p.archetype.value), p.name, p.stance
         )
         persona_pairs.append((row, p))
     session.commit()
     ```
  4. `debate.status = DebateStatus.RUNNING; session.commit()`.
  5. `transcript: list[_TurnRecord] = []`.
  6. For `round_number in range(1, settings.max_rounds + 1)`:
     - Snapshot `prior = list(transcript)` (rounds `< round_number`).
     - Define an inner coroutine that runs one persona's turn in a thread and returns
       `(persona_index, orm_persona, TurnResult)`:
       ```python
       async def _one(index: int, row, schema_p):
           messages = _build_turn_messages(
               decision=debate.decision, context=debate.context,
               stance=schema_p.stance, transcript=prior, round_number=round_number,
           )
           result = await asyncio.to_thread(
               llm.run_turn,
               tier="personas",
               messages=messages,
               guardrails=guardrails,
               round_number=round_number,
               persona_index=index,
               max_tokens=max_tokens_per_turn,
               system=system_prompt_for(schema_p.archetype),
           )
           return index, row, result
       ```
     - Create tasks for all three personas; iterate `asyncio.as_completed(tasks)`; for each finished
       result, persist immediately and commit (persist-as-it-lands):
       ```python
       status = TurnStatus.OK if result.status == "ok" else TurnStatus.SKIPPED
       repo.add_turn(session, debate.id, row.id, round=round_number,
                     content=result.text or "", status=status)
       session.commit()
       ```
       Collect `(index, row, result)` into a `round_results` list.
     - After the round, append OK turns to `transcript` **in persona order** (sort by index) so the
       next round reads a deterministic transcript:
       ```python
       for index, row, result in sorted(round_results, key=lambda x: x[0]):
           if result.status == "ok":
               transcript.append(_TurnRecord(row.name, row.archetype.value, round_number, result.text))
       ```
  7. `debate.status = DebateStatus.COMPLETED; session.commit()`.
  8. `session.refresh(debate)` (so `debate.turns` is populated) OR re-sort — simplest: return
     `repo.get_debate(session, debate.id)` which loads turns in transcript order. Return that.
- **PATTERN**: injection defaults mirror `LLMService.__init__` (llm.py:38); repo calls mirror
  `test_repository._build_full_debate` (test_repository.py:10-37).
- **IMPORTS**: add `from app.core.config import get_settings`.
- **GOTCHA — two `Archetype` enums**: `assign_personas` returns `app.schemas.persona.Persona` whose
  `.archetype` is `app.schemas.persona.Archetype`; `repo.add_persona` needs `app.models.enums.Archetype`.
  Convert with `ModelArchetype(p.archetype.value)`. Conversely `system_prompt_for` is keyed by the
  **schemas** enum, so pass the schema persona's `p.archetype` there (NOT the ORM row's).
- **GOTCHA — non-null `content`**: `Turn.content` is NOT nullable; a skipped `TurnResult.text` is `""`
  — persist `result.text or ""`, never `None`.
- **GOTCHA — Session thread-safety**: NEVER call `repo.*` or touch `session` inside the
  `asyncio.to_thread` target. Only `llm.run_turn` runs in the worker thread; all DB writes happen on
  the orchestrator's thread after `await`. (SQLAlchemy `Session` is not thread-safe.)
- **GOTCHA — guardrail token race (accepted MVP tradeoff)**: `DebateGuardrails.record_usage` mutates
  `tokens_spent` from inside the three concurrent `run_turn` threads, so the per-debate **token** cap
  is a soft ceiling under concurrency. The **persona** and **round** caps are unaffected (they check
  immutable per-call args). This is acceptable for the MVP (3 concurrent turns, 200k budget). Do NOT
  add locking — note it in a comment; if it ever matters, it's a follow-up, not this ticket.
- **GOTCHA — DEC-003**: rounds come from `settings.max_rounds` only. Do not add early-stop/convergence.
- **VALIDATE**: `cd backend && .venv/bin/ruff check app/services/orchestrator.py`

### CREATE `backend/tests/test_orchestrator.py` — unit tests

- **IMPLEMENT**: A `StubLLM` (duck-typed, mirrors `test_llm.make_service` injection idea) plus tests
  driven with `asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))`:
  ```python
  import asyncio
  from types import SimpleNamespace

  from app.core.guardrails import DebateGuardrails
  from app.models.enums import Archetype, DebateStatus, TurnStatus
  from app.repositories import debates as repo
  from app.services.llm import TurnResult
  from app.services.orchestrator import run_debate


  class StubLLM:
      """Synchronous run_turn stub — no network. Records calls; can force skips."""
      def __init__(self, skip=frozenset()):
          self.calls = []
          self.skip = set(skip)  # set of (round_number, persona_index) to skip

      def run_turn(self, *, tier, messages, guardrails, round_number,
                   persona_index, max_tokens=1024, system=None):
          self.calls.append(SimpleNamespace(
              round=round_number, index=persona_index,
              text=messages[0].content, system=system))
          if (round_number, persona_index) in self.skip:
              return TurnResult(status="skipped", model="stub", reason="forced")
          return TurnResult(status="ok", model="stub",
                            text=f"r{round_number}p{persona_index}", tokens_in=1, tokens_out=1)


  def make_guardrails(**over):
      d = dict(max_personas=3, max_rounds=2, max_tokens_per_debate=100_000)
      d.update(over)
      return DebateGuardrails(**d)
  ```
  Tests:
  1. `test_two_rounds_three_personas_all_ok(session)`:
     - `debate = repo.create_debate(session, "Adopt SQLite?", "dev tooling")`
     - `stub = StubLLM()`; `result = asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))`
     - Assert `len(result.personas) == 3` and archetypes == {ADVOCATE, SKEPTIC, PRAGMATIST}.
     - Assert `len(result.turns) == 6` and `[t.round for t in result.turns] == [1,1,1,2,2,2]`.
     - Assert every turn `status == TurnStatus.OK`, `result.status == DebateStatus.COMPLETED`.
     - Assert `len(stub.calls) == 6`; the set of `(c.round, c.index)` == all pairs in `{1,2}×{0,1,2}`.
  2. `test_round_two_sees_round_one_transcript(session)`:
     - Run all-OK. Filter `stub.calls` for `round == 2`; assert each round-2 call's `text` contains a
       round-1 marker (e.g. `"r1p0"` / `"[Round 1]"`), and round-1 calls do NOT contain any `"r1"`
       transcript line (their prompt has no prior discussion). This proves sequential rounds + transcript threading.
  3. `test_forced_skip_still_completes(session)`:
     - `stub = StubLLM(skip={(1, 1)})` (skeptic skips in round 1).
     - Run; assert `result.status == DebateStatus.COMPLETED`, `len(result.turns) == 6`.
     - Assert exactly one turn has `status == TurnStatus.SKIPPED` with `content == ""`; the rest OK.
     - Assert round 2 still ran (three round-2 calls in `stub.calls`) — a skip does not abort the debate.
- **PATTERN**: `session` fixture from `conftest.py`; injection/stub style from `test_llm.py`;
  full-debate assertions from `test_repository.py`.
- **GOTCHA — no `pytest-asyncio`**: it is NOT a dependency. Drive the coroutine with
  `asyncio.run(...)`; do NOT use `@pytest.mark.asyncio`.
- **GOTCHA — call-order nondeterminism**: the three per-round turns run concurrently, so `stub.calls`
  order within a round is not deterministic. Assert on **sets / membership**, never on index order
  within a round.
- **VALIDATE**: `cd backend && .venv/bin/pytest tests/test_orchestrator.py -q`

### UPDATE Decision Log (post-merge bookkeeping — do at commit time, not in code)

- **IMPLEMENT**: After the change is committed, set DEC-003 *Implemented by* and the Implementation
  Tracker row to `KAN-7 · <commit sha>` on Confluence page 1015810 (mirrors how DEC-005/007/008 rows
  read). This is part of the Definition of Done, not a code change.
- **VALIDATE**: manual — confirm the tracker row for DEC-003 is no longer `—`.

---

## TESTING STRATEGY

### Unit Tests
`backend/tests/test_orchestrator.py`, using the in-memory `session` fixture and an injected `StubLLM`
(no network, no real sleep). Three core tests: all-OK 2×3 debate, transcript threading (round 2 sees
round 1), forced-skip resilience. Mirror the fixture/stub/assertion patterns already in
`test_llm.py` and `test_repository.py`.

### Integration Tests
None in this ticket — HTTP wiring and the orchestrator→judge integration are TICKET-5/TICKET-6. Keep
`run_debate` free of HTTP concerns so those tickets can integrate against its return value.

### Edge Cases
- All three personas skip in a round → debate still reaches `COMPLETED` with 6 persisted turns (all
  round turns SKIPPED, `content=""`). (Extend test 3 or add a variant with `skip={(1,0),(1,1),(1,2)}`.)
- A skip in round 1 must not remove that persona from round 2 — round 2 still dispatches three turns.
- Empty `context` (`None`) → prompt builder must not crash or print "None"; omit the context line.
- Round 1 transcript is empty → prompt builder emits a "no prior discussion" note, not a stray `[Round 0]`.

### E2E / Browser Automation
**Not applicable.** This is a headless backend service with no HTTP surface or UI in EPIC-A/TICKET-4
(DEC-004 watch-only UI is EPIC-C). Skip Level 5. The end-to-end HTTP flow is validated in TICKET-6.

---

## VALIDATION COMMANDS

Run from the repo root unless noted. The backend venv lives at `backend/.venv`.

### Level 1: Syntax & Style
```bash
cd backend && .venv/bin/ruff check app/services/orchestrator.py tests/test_orchestrator.py
cd backend && .venv/bin/ruff format --check app/services/orchestrator.py tests/test_orchestrator.py
```

### Level 2: Unit Tests (this feature)
```bash
cd backend && .venv/bin/pytest tests/test_orchestrator.py -q
```

### Level 3: Full Suite (no regressions)
```bash
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/ruff check .
```

### Level 4: Manual Validation (optional, requires ANTHROPIC_API_KEY)
Drive the real orchestrator against a live model from a throwaway script in the scratchpad (not
committed): create a debate via `repo.create_debate` inside `get_session()`, `asyncio.run(run_debate(...))`,
then print `get_debate(...).turns`. Confirm 6 turns across 2 rounds and round-2 content references
round-1 points. Skip if no API key — the unit tests fully cover the logic with a stub.

### Level 5: E2E / Browser Automation
N/A — headless backend, no UI (see Testing Strategy).

### Level 6: Additional Validation
Run `/validate` (project quality gate) before opening the PR.

---

## ACCEPTANCE CRITERIA

- [ ] `run_debate(session, debate)` runs `settings.max_rounds` (= 2, DEC-003) rounds; the three
      persona turns within a round run **concurrently** (via `asyncio.to_thread`).
- [ ] Rounds run **sequentially**; round N's prompts include the transcript of rounds `< N`.
- [ ] Each completed turn is persisted as it lands (per-turn `commit`); failed/blocked turns persist
      as `SKIPPED` (`content=""`) and the debate continues.
- [ ] Produces the full transcript the judge consumes (returns the `Debate` with `turns` in transcript
      order); no HTTP concerns in the module.
- [ ] Tests cover 2 rounds × 3 personas (6 turns) and a forced skip that still completes the debate.
- [ ] All validation commands pass; no regressions in the existing suite.
- [ ] Code follows project conventions (line length 100, ruff-clean, keyword-only service calls,
      module docstring citing DECs).
- [ ] Commit carries `Decisions: DEC-003` (+ 001/002/007/008); DEC-003 *Implemented by* updated in the
      Decision Log.

---

## COMPLETION CHECKLIST

- [ ] `orchestrator.py` created (`_build_turn_messages` + `run_debate`).
- [ ] `test_orchestrator.py` created with the three core tests (+ edge variants).
- [ ] Level 1 ruff check/format pass.
- [ ] Level 2 orchestrator tests pass.
- [ ] Level 3 full suite + ruff on the whole backend pass (no regressions).
- [ ] Level 4 manual smoke done or explicitly skipped (no API key).
- [ ] Level 5 N/A (headless).
- [ ] Acceptance criteria all met.
- [ ] `/code-review` clean; commit message carries the `Decisions:` trailer.
- [ ] Decision Log DEC-003 *Implemented by* updated to `KAN-7 · <commit>`.

---

## NOTES

**Design decisions & trade-offs**

- **Sync→async bridge (`asyncio.to_thread`)**: chosen over a native async Anthropic client to avoid
  touching KAN-6's merged synchronous `LLMService`. `run_debate` is a coroutine; tests drive it with
  `asyncio.run`. Not a new DEC — pure implementation detail under DEC-003.
- **Writes on the orchestrator thread only**: SQLAlchemy `Session` is not thread-safe, so only
  `llm.run_turn` runs in worker threads; all `repo.*` + `commit` calls happen on the awaiting thread.
  This also keeps SQLite (single-writer) happy.
- **Persist-as-it-lands via `as_completed`**: satisfies the acceptance criterion literally — each turn
  commits the moment its LLM call returns, so a later failure can never lose an earlier completed turn.
  The in-memory `transcript` is re-ordered by persona index after the round for deterministic round-N
  prompts (persisted order stays completion-order, which `get_debate` re-sorts by `(round, created_at, id)`).
- **Guardrail token race**: accepted MVP soft-ceiling (documented inline). Not worth a lock for three
  concurrent turns against a 200k-token budget.
- **`run_debate` signature**: takes `session` (for test injectability against the `session` fixture and
  for TICKET-6 to pass its request-scoped session) plus injectable `llm`/`guardrails`. It internally
  assigns/persists personas so it's a self-contained engine — TICKET-6 will just
  `create_debate → run_debate → judge`.
- **Status lifecycle**: `PENDING → RUNNING → COMPLETED`. `FAILED` is left for a genuine unexpected
  exception path; skips are normal and do not set `FAILED`. (Optional: wrap the loop in try/except to
  set `FAILED` and re-raise — nice-to-have, not required by the AC.)

**Confidence: 8.5/10** for one-pass success. The main risks are the two-`Archetype`-enum conversion and
the concurrency/session discipline — both are called out explicitly with the exact conversions and the
"writes on the main thread" rule. The stub-based tests are deterministic if assertions use sets, not
per-round ordering.
