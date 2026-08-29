# Code Review — KAN-7 / TICKET-4: Debate Orchestrator

**Commit:** `1ee6195` (branch `KAN-7`)
**Reviewed:** `backend/app/services/orchestrator.py`, `backend/tests/test_orchestrator.py`
(the plan doc `.claude/plans/kan-7-debate-orchestrator.md` is non-code, not reviewed for defects)

**Stats:**
- Files Modified: 0
- Files Added: 3 (1 source, 1 test, 1 plan)
- Files Deleted: 0
- New lines: 880 (191 source + 134 test + 555 plan)
- Deleted lines: 0

---

## Verification performed

- **Full suite:** `38 passed` (incl. 4 new orchestrator tests). `ruff check app/ tests/test_orchestrator.py` and `ruff format --check` clean on authored files.
- **Concurrency/session discipline:** confirmed only `llm.run_turn` executes inside `asyncio.to_thread`; every `repo.*` + `session.commit()` runs on the awaiting thread. `expire_on_commit=False` (session factory + conftest) keeps `debate`/`row` attributes usable across the mid-debate commits — no `DetachedInstanceError` risk.
- **Closure capture:** `_one(..., _round=round_number, _prior=prior)` binds per-round values via default args (avoids the late-binding-in-loop trap; ruff B023 stays quiet). Enclosing captures (`debate`, `llm`, `guardrails`, `max_tokens_per_turn`) are loop-invariant.
- **Guardrail caps:** `persona_index` 0–2 < `max_personas` 3; `round_number` 1–2 ≤ `max_rounds` 2 — neither trips `CapExceededError`.
- **Enum bridge:** `ModelArchetype(p.archetype.value)` (schemas→models) and `system_prompt_for(schema_p.archetype)` (schemas-keyed) both correct.
- **Non-null content:** skipped turns persist `result.text or ""` — never `None`.

---

## Findings

### low — unused field on `_TurnRecord`
```
severity: low
file: backend/app/services/orchestrator.py
line: 46
issue: `_TurnRecord.archetype` is populated but never read.
detail: `_build_turn_messages` only uses `rec.round`, `rec.name`, `rec.content`. The
  `archetype` field (set at orchestrator.py:182 from `row.archetype.value`) is dead data —
  it adds a field and a `.value` access that never affect output. Minor clarity cost; not a bug.
suggestion: Either drop the field (and the `row.archetype.value` arg at the append site) for
  simplicity, or start prefixing transcript lines with the archetype if that context is wanted
  in the persona prompt (e.g. `[Round r] {name} ({archetype}): ...`). Prefer the latter — it
  gives the model the role label cheaply and makes the field earn its place.
```

### low — debate can stay `RUNNING` if a turn raises unexpectedly
```
severity: low
file: backend/app/services/orchestrator.py
line: 155
issue: An unexpected exception from a turn coroutine propagates out of `run_debate` while the
  debate row is still `RUNNING`; it is never transitioned to `FAILED`.
detail: By contract `LLMService.run_turn` never raises (it catches APIError/CapExceededError and
  degrades to a skipped TurnResult), so this cannot happen on the documented path — which is why
  it is low, not high. But a contract violation (e.g. the Anthropic client raising a non-APIError,
  or a bug in `system_prompt_for`) would abort the `as_completed` loop with the row left in
  RUNNING, leaving a "stuck" debate. The plan explicitly deferred FAILED-handling as optional, so
  this is a known, accepted gap rather than an oversight.
suggestion: Optional hardening for TICKET-6 (where an HTTP caller needs a terminal state): wrap
  the round loop in `try/except`, set `debate.status = DebateStatus.FAILED; session.commit()`, and
  re-raise. Not required to meet this ticket's acceptance criteria.
```

---

## Notes (acknowledged, not defects)

- **Guardrail token race** (`guardrails.record_usage`/`check` mutating `tokens_spent` from three
  concurrent `to_thread` calls) is documented inline as an accepted MVP soft-ceiling; the persona
  and round caps are unaffected. Correct call for 3 concurrent turns against a 200k budget.
- **Persisted intra-round order is completion order** (nondeterministic), re-sorted by
  `get_debate` as `(round, created_at, id)`; the next round's transcript is rebuilt in persona-index
  order, so round-N prompts are deterministic. Tests assert on round order / sets only — correct.

---

## Decision compliance (CLAUDE.md)

- Honors **DEC-003** (rounds sourced from `settings.max_rounds`; no convergence heuristic added),
  **DEC-001/002** (reuses `assign_personas`, fixed set), **DEC-008** (per-turn commit),
  **DEC-007** (`tier="personas"`), **DEC-005** (no ownership).
- Commit carries `Decisions: DEC-003, DEC-001, DEC-002, DEC-007, DEC-008`.
- **Outstanding DoD item:** DEC-003 *Implemented by* row in the Decision Log is still `—`; update to
  `KAN-7 · 1ee6195`.

---

## Verdict

**Pass.** No critical/high/medium defects. Two low-severity items (one dead field, one
pre-acknowledged FAILED-status gap), neither blocking. The concurrency + persistence design is
sound and the tests are deterministic.
