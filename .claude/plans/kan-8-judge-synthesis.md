# Feature: KAN-8 (TICKET-5) — Judge synthesis with structured output + repair retry

The following plan should be complete, but it's important that you validate documentation and
codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files.

## Feature Description

Add the **judge** — the final stage of the EPIC-A debate engine. After the orchestrator
(`run_debate`, KAN-7) produces a persisted transcript, `judge(debate)` runs one **Opus** call on
the full transcript and synthesizes a balanced `Verdict`: a recommendation, the strongest case for
each option, and the key trade-offs. The verdict is **schema-validated** (Pydantic); on a schema
mismatch the judge does **exactly one repair retry**, then raises. The validated verdict is
persisted (via the existing repository) and linkable to its transcript.

## User Story

As a decision-maker
I want a balanced verdict with the strongest case for each option and the key trade-offs
So that I can decide with a clear head — and trust it links back to the debate.

## Problem Statement

The debate engine currently ends with a raw transcript (`Debate.turns`). There is no synthesis
step: nothing reads the whole debate and produces a decision-ready verdict, and nothing guarantees
the verdict has a machine-usable, schema-valid shape that the API (KAN-9) and future UI can render.

## Solution Statement

Introduce a Pydantic `Verdict` contract, a judge prompt, and a `judge()` service that:
1. Renders the full transcript (decision + context + persona turns) into an Opus prompt.
2. Requests a **JSON** verdict (prompt-constrained, not native structured outputs — see the design
   note below), parses it, and validates it against `Verdict`.
3. On a parse/validation failure, sends **one** repair turn quoting the bad output + the error, then
   re-validates. Second failure → raise `JudgeSchemaError`.
4. Exposes a thin persistence helper mapping the validated `Verdict` onto `repo.set_verdict`.

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: Medium
**Primary Systems Affected**: `backend/app/services` (judge), `backend/app/schemas` (verdict),
`backend/app/prompts` (judge prompt), `backend/app/services/llm.py` (add a judge-tier completion
primitive). No HTTP surface (that's KAN-9).
**Dependencies**: `anthropic` (already used), `pydantic` v2 (already used). No new deps.

**Governing decision:** **DEC-007** — *Model routing (Sonnet personas / Opus judge) + structured,
schema-validated verdict with one repair retry.* This plan realizes the judge half of DEC-007.
It does **not** contradict any Accepted DEC. `docs/architecture.md` currently marks
`app/services/judge.py` and the verdict schema as ⏳ *planned (TICKET-5)*; the `/commit` step will
flip those to ✅, add a Change Log row, and update the Decision Log "Implemented by" for DEC-007.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — READ THESE BEFORE IMPLEMENTING

- `backend/app/services/llm.py` (lines 35–171) — Why: `LLMService` pattern. `model_for(tier)`
  resolves `"judge"` → `claude-opus-4-8` (DEC-007). `run_turn` shows the retry/backoff loop and the
  text-extraction idiom `"".join(b.text for b in response.content if b.type == "text")`. **Note:**
  `run_turn` *never raises* (degrades to skipped) — the judge needs a primitive that *raises* on
  hard failure and carries no guardrail/persona plumbing, so add a new `complete()` method here.
- `backend/app/core/config.py` (lines 17–21) — Why: `model_judge = "claude-opus-4-8"` already set;
  `get_settings()` lru-cached singleton. No new settings needed.
- `backend/app/models/verdict.py` (whole file) — Why: the ORM target. Fields: `recommendation: str`,
  `cases: JSON` (comment: `[{"option": str, "argument": str}]`), `tradeoffs: JSON`. The comment
  literally says "shape validated in TICKET-5" — that shape is your Pydantic schema. Unique FK to
  `debates`.
- `backend/app/repositories/debates.py` (lines 63–107) — Why: `set_verdict(session, debate_id,
  recommendation, cases, tradeoffs)` already **upserts** the single verdict (respects the unique FK).
  `get_debate` returns turns in transcript order and eager-loads `personas`, `turns`, `verdict`.
- `backend/app/models/debate.py` + `models/turn.py` + `models/persona.py` — Why: `judge()` reads
  `debate.decision`, `debate.context`, `debate.personas` (`.id`, `.name`, `.archetype`), and
  `debate.turns` (`.round`, `.content`, `.status`, `.persona_id`). Map `persona_id → name`.
- `backend/app/models/enums.py` (lines 12–16) — Why: `TurnStatus.OK` / `TurnStatus.SKIPPED`. The
  judge must include only `OK` turns in the transcript (skipped turns persist empty content).
- `backend/app/prompts/personas.py` (lines 1–92) — Why: prompt-module style to mirror (module
  docstring citing DEC, frozen constants, small builder functions).
- `backend/app/services/personas.py` — Why: service style (docstring citing DEC, pure function,
  `ValueError` on empty input).
- `backend/app/schemas/persona.py` — Why: Pydantic v2 schema style (`BaseModel`, `Field(...,
  description=...)`, str `Enum`).
- `backend/app/services/orchestrator.py` (lines 51–83, 121–191) — Why: how the transcript is built
  and rendered as `[Round N] {name}: {content}`; mirror that rendering in the judge prompt so the
  judge sees the same shape. Also the model for a `session`-taking service signature.
- `backend/tests/test_llm.py` (whole file) — Why: mocked-Anthropic-client test pattern
  (`MagicMock` client, `make_response`, `transient_error`, `sleep=lambda _: None`). Use for the new
  `complete()` unit tests.
- `backend/tests/test_orchestrator.py` (lines 11–48) — Why: `StubLLM` injection pattern. Mirror it
  as `StubJudgeLLM` (records calls, pops queued responses) for the judge repair-loop tests.
- `backend/tests/test_repository.py` (lines 10–37) — Why: `_build_full_debate` helper to construct a
  real `Debate` (create → 3 personas → 6 turns) via the `session` fixture — reuse for judge tests.
- `backend/tests/conftest.py` — Why: the `session` fixture (in-memory SQLite, FKs on).

### New Files to Create

- `backend/app/schemas/verdict.py` — Pydantic `Case` + `Verdict` contracts (the DEC-007 shape).
- `backend/app/prompts/judge.py` — judge system prompt + user-message builder + repair-message
  builder.
- `backend/app/services/judge.py` — `judge(debate, *, llm=None) -> Verdict` (synthesis + one repair
  retry + raise) and `persist_verdict(session, debate, verdict) -> models.Verdict` (maps onto
  `repo.set_verdict`). Plus `JudgeError` / `JudgeSchemaError`.
- `backend/tests/test_judge.py` — unit tests (valid / malformed-then-valid / malformed-twice /
  tier / transcript-in-prompt / persistence).

### Files to Modify

- `backend/app/services/llm.py` — add `complete(*, tier, messages, system=None, max_tokens=2048)
  -> str`: single guarded completion that **raises** on API error (bounded retry/backoff mirroring
  `run_turn`, but re-raises after exhausting instead of skipping). No guardrails/persona plumbing.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- Load the **`claude-api`** skill (already loaded this session). Key facts it confirms:
  - Judge model id is `claude-opus-4-8` (already in `config.py` via `model_judge`; resolve through
    `LLMService.model_for("judge")` — do **not** hardcode the string in the judge).
  - Pydantic + `client.messages.parse()` / `output_config.format` are the *native* structured-output
    paths. **Do NOT use them here** — see the design note. Use plain `messages.create` + manual
    `json.loads` + `Verdict.model_validate`, which is what makes the repair-retry path real and
    unit-testable with a stub that returns malformed-then-valid text.
  - `max_tokens` for a small structured verdict: `2048` is ample; non-streaming is fine.

### Patterns to Follow

**Design note — why prompt-constrained JSON, not native structured outputs (DEC-007):** DEC-007
mandates "structured, schema-validated verdict + one repair retry." Native structured outputs
(`output_config.format`) would make malformed output effectively impossible and the repair path
dead/untestable. The acceptance test ("valid passes; malformed-then-valid triggers one repair;
malformed-twice raises") only works if the judge validates model text itself. So: prompt the judge
to emit a JSON object, parse + validate with Pydantic, and repair once on failure. This also matches
the existing `LLMService` (plain `messages.create`, text extraction).

**Naming conventions:** snake_case functions/modules; module docstring citing the DEC/ticket;
Pydantic schemas in `schemas/`, prompt constants + builders in `prompts/`, orchestration in
`services/`. Mirror `services/personas.py` (pure, raises `ValueError` on bad input).

**Text extraction (from `llm.py:131`):**
```python
text = "".join(block.text for block in response.content if block.type == "text")
```

**Retry/raise (adapt `run_turn`'s loop, but re-raise):**
```python
last_error: Exception | None = None
for attempt in range(self._max_retries + 1):
    try:
        response = self._client.messages.create(model=model, max_tokens=max_tokens,
            messages=payload, **({"system": system} if system is not None else {}))
    except APIError as exc:
        last_error = exc
        if attempt < self._max_retries:
            self._sleep(self._backoff_base * (2 ** attempt)); continue
        raise
    return "".join(b.text for b in response.content if b.type == "text")
raise last_error  # unreachable; satisfies type-checkers
```

**LLM injection for tests (from `test_orchestrator.py:11`):** services take `llm: LLMService | None
= None`, defaulting to `get_llm_service()`. Tests pass a stub exposing the same method.

**Verdict → ORM mapping (repo already exists):**
```python
repo.set_verdict(session, debate.id, recommendation=v.recommendation,
    cases=[c.model_dump() for c in v.cases], tradeoffs=v.tradeoffs)
```
`cases` becomes `list[dict]` — exactly the JSON shape `test_repository` asserts.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — the Verdict contract
Define `Case` and `Verdict` Pydantic schemas so the judge and persistence code share one shape.

### Phase 2: Prompt
Write the judge system prompt (role, JSON-only instruction, exact field spec) and builders for the
initial user message (transcript) and the repair message (bad output + error).

### Phase 3: Core — LLM primitive + judge service
Add `LLMService.complete`. Implement `judge()` (synthesis + one repair) and `persist_verdict()`.

### Phase 4: Testing & Validation
Unit-test `complete` (mocked client) and `judge` (stub llm, all three repair outcomes) plus
persistence round-trip. Run the full gate.

---

## STEP-BY-STEP TASKS

### CREATE `backend/app/schemas/verdict.py`
- **IMPLEMENT**: Two Pydantic v2 models.
  ```python
  from pydantic import BaseModel, Field

  class Case(BaseModel):
      """The strongest case for one option (DEC-007)."""
      option: str = Field(..., min_length=1, description="The option this case argues for")
      argument: str = Field(..., min_length=1, description="Strongest argument for the option")

  class Verdict(BaseModel):
      """Judge's synthesized outcome — schema-validated (DEC-007). Mirrors models.Verdict."""
      recommendation: str = Field(..., min_length=1, description="Balanced recommendation")
      cases: list[Case] = Field(..., min_length=1, description="Strongest case per option")
      tradeoffs: list[str] = Field(..., min_length=1, description="Key trade-offs")
  ```
- **PATTERN**: `backend/app/schemas/persona.py` (Field + description).
- **GOTCHA**: Do NOT set `extra="forbid"` — the model may emit harmless extra keys; forbidding them
  would force needless repairs. Keep `min_length` on required fields so a plausible-but-empty or
  missing-field payload fails validation (this is what the malformed test exercises). Field shapes
  MUST match `models/verdict.py` (`cases` = list of `{option, argument}`; `tradeoffs` = list[str]).
- **VALIDATE**: `cd backend && python -c "from app.schemas.verdict import Verdict; Verdict.model_validate({'recommendation':'x','cases':[{'option':'a','argument':'b'}],'tradeoffs':['t']})"`

### CREATE `backend/app/prompts/judge.py`
- **IMPLEMENT**:
  - `JUDGE_SYSTEM_PROMPT: str` — role ("impartial judge synthesizing a multi-persona decision
    debate"), instruction to weigh all sides, and a hard rule: **respond with ONLY a single JSON
    object, no prose, no code fences**, matching the field spec. Enumerate fields exactly:
    `recommendation` (string), `cases` (array of `{"option": string, "argument": string}`, one per
    distinct option debated), `tradeoffs` (array of strings). "Never invent facts; ground the
    verdict in the transcript."
  - `build_judge_messages(*, decision, context, transcript_lines) -> list[ChatMessage]` — returns a
    single user `ChatMessage` (import `ChatMessage` from `app.schemas.chat`). Include the decision,
    optional context, the rendered transcript, and a closing "Return the verdict as JSON now."
  - `build_repair_message(*, bad_output, error) -> ChatMessage` — a user `ChatMessage` quoting the
    invalid output and the validation/parse error, instructing: "Your previous response was not
    valid. Fix it and return ONLY the corrected JSON object."
- **PATTERN**: `backend/app/prompts/personas.py` (module docstring citing DEC-007/TICKET-5; frozen
  constant + small builder fns). Transcript line format mirrors `orchestrator._build_turn_messages`:
  `f"[Round {round}] {name}: {content}"`.
- **IMPORTS**: `from app.schemas.chat import ChatMessage`.
- **GOTCHA**: Keep the transcript rendering here (or in the service) consistent with the
  orchestrator's `[Round N] {name}: {content}`. Only include `OK` turns.
- **VALIDATE**: `cd backend && python -c "from app.prompts.judge import JUDGE_SYSTEM_PROMPT, build_judge_messages, build_repair_message; print(bool(JUDGE_SYSTEM_PROMPT))"`

### UPDATE `backend/app/services/llm.py` — add `complete`
- **IMPLEMENT**: A `complete(self, *, tier: Tier, messages: list[ChatMessage], system: str | None =
  None, max_tokens: int = 2048) -> str` method on `LLMService`. Resolve model via
  `self.model_for(tier)`; build the payload as in `run_turn`; run the bounded retry loop that
  **re-raises** the `APIError` after exhausting retries (see the retry/raise snippet above); return
  extracted text. Log an info line on success mirroring `run_turn`'s cost log (tokens in/out).
- **PATTERN**: `run_turn` (lines 102–156) — reuse its retry/backoff and text extraction, but this
  method carries **no guardrails**, **no persona_index**, and **raises** instead of skipping.
- **IMPORTS**: none new (`APIError`, `ChatMessage` already imported).
- **GOTCHA**: This is the ONLY new public surface on `LLMService`. Don't touch `run_turn`. The judge
  call is intentionally outside the debate `DebateGuardrails` (those cap the persona rounds; the
  judge is a single post-debate synthesis). Keep `self._sleep` injectable so tests don't sleep.
- **VALIDATE**: `cd backend && python -m pytest tests/test_llm.py -q`

### CREATE `backend/app/services/judge.py`
- **IMPLEMENT**:
  ```python
  """Judge synthesis with structured output + repair retry (TICKET-5 / KAN-8, DEC-007)."""
  import json, logging
  from pydantic import ValidationError
  from sqlalchemy.orm import Session
  from app.models.debate import Debate
  from app.models.enums import TurnStatus
  from app.models.verdict import Verdict as VerdictRow
  from app.prompts.judge import JUDGE_SYSTEM_PROMPT, build_judge_messages, build_repair_message
  from app.repositories import debates as repo
  from app.schemas.verdict import Verdict
  from app.services.llm import LLMService, get_llm_service

  class JudgeError(Exception): ...
  class JudgeSchemaError(JudgeError):
      """Raised when the verdict is still schema-invalid after one repair retry."""

  def _render_transcript(debate: Debate) -> list[str]:
      names = {p.id: p.name for p in debate.personas}
      return [f"[Round {t.round}] {names.get(t.persona_id, '?')}: {t.content}"
              for t in debate.turns if t.status is TurnStatus.OK and t.content]

  def _parse_and_validate(text: str) -> Verdict:
      # Strict-ish: strip optional ```json fences, then json.loads + Verdict.model_validate.
      # Any json.JSONDecodeError or ValidationError propagates to the repair caller.
      ...

  def judge(debate: Debate, *, llm: LLMService | None = None, max_tokens: int = 2048) -> Verdict:
      llm = llm or get_llm_service()
      messages = build_judge_messages(decision=debate.decision, context=debate.context,
                                      transcript_lines=_render_transcript(debate))
      first = llm.complete(tier="judge", messages=messages, system=JUDGE_SYSTEM_PROMPT,
                           max_tokens=max_tokens)
      try:
          return _parse_and_validate(first)
      except (json.JSONDecodeError, ValidationError) as err:
          logger.warning("judge verdict invalid, repairing: debate=%s error=%s", debate.id, err)
      # exactly one repair retry
      repair = messages + [ChatMessage(role="assistant", content=first),
                           build_repair_message(bad_output=first, error=str(err))]
      second = llm.complete(tier="judge", messages=repair, system=JUDGE_SYSTEM_PROMPT,
                            max_tokens=max_tokens)
      try:
          return _parse_and_validate(second)
      except (json.JSONDecodeError, ValidationError) as err:
          raise JudgeSchemaError(
              f"verdict invalid after one repair (debate={debate.id}): {err}") from err

  def persist_verdict(session: Session, debate: Debate, verdict: Verdict) -> VerdictRow:
      return repo.set_verdict(session, debate.id, recommendation=verdict.recommendation,
          cases=[c.model_dump() for c in verdict.cases], tradeoffs=verdict.tradeoffs)
  ```
- **PATTERN**: `services/personas.py` (pure fn + docstring) and `services/orchestrator.py`
  (`llm or get_llm_service()`, `session`-taking persistence).
- **IMPORTS**: also `from app.schemas.chat import ChatMessage`; `logger = logging.getLogger(__name__)`.
- **GOTCHA**:
  - **Exactly one** repair — the second failure raises; there is NO third call. Assert this in tests
    by counting stub calls == 2.
  - Build the repair conversation as `user(prompt) → assistant(first) → user(repair)`; that's a
    normal multi-turn (not a trailing prefill) and is allowed.
  - `_parse_and_validate` must raise `json.JSONDecodeError` for non-JSON and `ValidationError` for
    JSON-that-fails-schema — both are caught identically. Strip a leading ` ```json ` / trailing
    ` ``` ` fence if present before `json.loads`.
  - Keep `judge()` pure (no session, no persistence) so KAN-9 composes `judge()` then
    `persist_verdict()`. Do not persist inside `judge()`.
- **VALIDATE**: `cd backend && python -m pytest tests/test_judge.py -q`

### CREATE `backend/tests/test_judge.py`
- **IMPLEMENT** a `StubJudgeLLM` (records `.calls`, pops queued `responses`) and tests:
  1. `test_valid_verdict_passes` — one valid JSON response → returns `Verdict`; `len(stub.calls)==1`.
  2. `test_non_json_then_valid_triggers_one_repair` — `["not json at all", VALID_JSON]` → returns
     `Verdict`; `len(stub.calls)==2`; assert the 2nd call's messages contain the repair text.
  3. `test_schema_invalid_then_valid_triggers_one_repair` — `['{"recommendation":""}', VALID_JSON]`
     (JSON-parseable but fails `min_length`/missing fields) → returns `Verdict`; 2 calls.
  4. `test_malformed_twice_raises` — `["not json", "still not json"]` → `pytest.raises(JudgeSchemaError)`;
     `len(stub.calls)==2` (no third call).
  5. `test_judge_uses_opus_tier` — assert every `stub.calls[i].tier == "judge"`.
  6. `test_transcript_and_decision_in_prompt` — build a real debate (reuse `_build_full_debate`
     shape via the `session` fixture) and assert the user message contains the decision string and a
     turn's content; assert SKIPPED/empty turns are excluded.
  7. `test_persist_verdict_round_trips` — `persist_verdict(session, debate, v)` then
     `repo.get_debate(session, debate.id).verdict` matches recommendation/cases/tradeoffs.
- **PATTERN**: `test_orchestrator.StubLLM` (stub + `SimpleNamespace` call records) and
  `test_repository._build_full_debate` (real Debate via `session`).
- **IMPORTS**: `from types import SimpleNamespace`; `from app.services.judge import judge,
  persist_verdict, JudgeSchemaError`; `from app.schemas.verdict import Verdict`; repo + enums.
- **GOTCHA**: Define `VALID_JSON` as a JSON **string** (what the model returns), e.g.
  `json.dumps({"recommendation": "...", "cases": [{"option":"A","argument":"..."}], "tradeoffs":["..."]})`.
  For tests that need a `Debate`, use the `session` fixture and repo helpers — `judge()` reads
  `.personas`, `.turns`, `.decision`, `.context`.
- **VALIDATE**: `cd backend && python -m pytest tests/test_judge.py -q`

### (Optional) ADD one `complete` unit test to `backend/tests/test_llm.py`
- **IMPLEMENT**: `test_complete_returns_text_and_raises_on_persistent_error` — mocked client returns
  text (assert extracted string) and, with `side_effect=transient_error()`, assert
  `pytest.raises(APIError)` after `_max_retries+1` attempts.
- **PATTERN**: existing `test_llm.py` helpers (`make_service`, `make_response`, `transient_error`).
- **VALIDATE**: `cd backend && python -m pytest tests/test_llm.py -q`

---

## TESTING STRATEGY

### Unit Tests
- `judge()` repair loop: the three DEC-007 outcomes (valid / one-repair / raise), driven by a stub
  LLM that returns queued strings — **no network**.
- `LLMService.complete`: mocked Anthropic client (mirror `test_llm.py`): returns text; raises after
  exhausting retries; routes to the `judge` tier model (`claude-opus-4-8`).
- Schema: `Verdict.model_validate` accepts the canonical shape and rejects missing/empty fields.

### Integration Tests
- `persist_verdict` round-trip through the real `session` fixture + `repo.get_debate` (verifies the
  Pydantic→ORM JSON mapping and the unique-FK upsert). Full end-to-end (POST → debate → judge →
  persisted verdict) is **KAN-9's** integration test, not this ticket's.

### Edge Cases
- Model wraps JSON in ```json fences → stripped and parsed (or repaired if truly malformed).
- Transcript with SKIPPED/empty turns → excluded from the prompt.
- A debate where the whole first round skipped → judge still gets round-2 OK turns.
- Exactly-one-repair invariant: never a third LLM call.

### E2E / Browser Automation
**N/A for this ticket.** EPIC-A is a headless backend (no UI, no HTTP route added here — DEC-004
watch-only; the API surface is KAN-9). There is nothing to drive in a browser. Behavior is fully
exercised by the unit/integration tests above (and later by `/verify` driving `judge()` on a
scripted debate).

---

## VALIDATION COMMANDS

Run from the repo root unless noted. Prefer the project's `/validate` skill, which wraps these.

### Level 1: Syntax & Style
```bash
cd backend && ruff check . && ruff format --check .
```

### Level 2: Unit Tests
```bash
cd backend && python -m pytest tests/test_judge.py tests/test_llm.py -q
```

### Level 3: Full Backend Suite (no regressions)
```bash
cd backend && python -m pytest -q
```

### Level 4: Manual Validation
```bash
cd backend && python -c "
from app.schemas.verdict import Verdict
from app.services.judge import judge, JudgeSchemaError
class Stub:
    def __init__(self, r): self.r=list(r); self.calls=[]
    def complete(self, *, tier, messages, system=None, max_tokens=2048):
        self.calls.append(tier); return self.r.pop(0)
from types import SimpleNamespace
d = SimpleNamespace(id='x', decision='Adopt SQLite?', context=None,
    personas=[SimpleNamespace(id='p', name='The Advocate')],
    turns=[SimpleNamespace(round=1, persona_id='p', content='Yes.', status=__import__('app.models.enums', fromlist=['TurnStatus']).TurnStatus.OK)])
import json
good = json.dumps({'recommendation':'Use SQLite','cases':[{'option':'SQLite','argument':'simple'}],'tradeoffs':['not for scale']})
s = Stub(['not json', good]); v = judge(d, llm=s)
print('repaired ok:', v.recommendation, 'calls=', len(s.calls))
"
```

### Level 5: E2E / Browser Automation
**N/A** — headless backend, no UI/route in this ticket (see Testing Strategy).

### Level 6: Additional Validation (Optional)
```bash
# Confirm the judge routes to the Opus tier (DEC-007) without a live call:
cd backend && python -c "from app.services.llm import LLMService; import unittest.mock as m; s=LLMService(sleep=lambda _:None); print(s.model_for('judge'))"
# expect: claude-opus-4-8
```

---

## ACCEPTANCE CRITERIA (from KAN-8)

- [ ] `judge(debate)` runs last on the full transcript using the **Opus tier** (`model_for("judge")`
      → `claude-opus-4-8`).
- [ ] Returns a schema-validated **Verdict** (Pydantic): `recommendation`, case-per-option
      (`cases`), `tradeoffs`.
- [ ] **Exactly one** repair retry on schema/parse mismatch, then hard error (`JudgeSchemaError`) if
      still invalid.
- [ ] Verdict persisted via `repo.set_verdict` and linkable to its transcript (`persist_verdict`).
- [ ] Tests: valid passes; malformed-then-valid triggers exactly one repair; malformed-twice raises.
- [ ] `/validate` green (ruff + full pytest); no regressions.
- [ ] Follows project conventions (schemas/prompts/services split; DEC-cited docstrings).

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task's `VALIDATE` command passed
- [ ] Level 1–3 validation commands pass (ruff clean, full suite green)
- [ ] Level 4 manual repair-loop check prints `repaired ok: ... calls= 2`
- [ ] Acceptance criteria all met
- [ ] `judge()` kept pure (no persistence) so KAN-9 can compose it
- [ ] Ready for `/code-review` → `/commit` (commit will flip architecture.md judge/verdict rows to
      ✅, add a Change Log row, and update DEC-007 "Implemented by" with the Jira key/commit)

---

## NOTES

- **Design trade-off (recorded, no new DEC needed):** prompt-constrained JSON + manual
  validate/repair, *not* native `output_config.format`. This is exactly what DEC-007 already
  specifies ("structured, schema-validated verdict + one repair retry"), so no new decision is
  raised. If a future ticket wants native structured outputs, that WOULD supersede the repair-retry
  behavior and must go through a new `DEC-xxx`.
- **Guardrails:** the judge call is intentionally outside `DebateGuardrails` (those cap persona
  rounds/tokens during the debate). The judge is a single post-debate Opus call. If cost-capping the
  judge is later desired, that's a follow-up, not this ticket.
- **KAN-9 seam:** the API will call `run_debate(session, debate)` → `judge(debate)` →
  `persist_verdict(session, debate, verdict)`. Keeping `judge()` free of HTTP/session concerns is
  what lets KAN-9 wire it behind `POST /api/debates` cleanly.
- **Model id source of truth:** never hardcode `claude-opus-4-8` in the judge — always resolve via
  `LLMService.model_for("judge")` (DEC-007, `config.model_judge`).
