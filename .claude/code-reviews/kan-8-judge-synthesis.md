# Code Review — KAN-8 (TICKET-5) Judge synthesis

Reviewed against: `CLAUDE.md` (ADR/DEC discipline), `docs/architecture.md` (living design doc),
`.claude/plans/kan-8-judge-synthesis.md`. Verified with the full backend suite and ruff.

**Verdict: PASS-with-nits.** Realizes DEC-007 (Opus judge + schema-validated verdict + exactly-one
repair retry) cleanly, follows the schema/prompt/service split, and is well-tested. No critical,
high, or blocking issues. Findings are one documentation-drift item (expected, handled by `/commit`)
plus two low data-robustness nits.

## Stats

- Files Modified: 2 (`app/services/llm.py`, `tests/test_llm.py`)
- Files Added: 4 code (`app/schemas/verdict.py`, `app/prompts/judge.py`, `app/services/judge.py`,
  `tests/test_judge.py`) + 1 doc (`.claude/plans/kan-8-judge-synthesis.md`)
- Files Deleted: 0
- New lines: 85 added across the 2 modified files (60 in `llm.py`, 25 in `test_llm.py`);
  374 lines across the 4 new code files (verdict 29, judge prompt 66, judge service 117, tests 162)
- Deleted lines: 1 (in `test_llm.py`)

## Validation performed

- `python -m pytest -q` → **48 passed** (15 in the KAN-8 target files). No regressions.
- `ruff check` + `ruff format --check` on all 6 touched files → **clean** (line-length 100 respected).
- Reproduced fence-stripping and schema-validation edge cases via `_parse_and_validate` probes
  (results cited in findings below).

---

## Findings

```
severity: low
file: docs/architecture.md
line: 48
issue: Architecture doc still marks judge.py + verdict schema as ⏳ planned (TICKET-5); this change realizes them.
detail: DEC-011 makes docs/architecture.md the living source of truth, updated by /commit whenever a
  change realizes a DEC. This change realizes the judge half of DEC-007, so row 48 (`app/services/judge.py`
  … ⏳ planned (TICKET-5)`) and the schemas row 53 ("verdict/debate schemas with T5/T6") are now stale.
  This is EXPECTED pre-commit drift, not a code defect — the plan explicitly defers the flip to /commit.
  Flagging so it is not forgotten.
suggestion: At /commit: flip the judge.py row (48) and the verdict-schema note (53) to ✅ (KAN-8);
  add a DEC-007 row to "Decisions realized in the current build" (currently only lists DEC-007 → KAN-6
  for model routing); add a dated Change Log row; mirror all of it to Confluence page 917506; and update
  DEC-007 "Implemented by" in the Decision Log. No architecture contradiction and no undocumented
  decision was introduced (prompt-constrained JSON + manual validate/repair is exactly what DEC-007
  specifies).
```

```
severity: low
file: backend/app/schemas/verdict.py
line: 29
issue: tradeoffs=list[str] with min_length=1 guards list length but not element emptiness — `["" ]` validates.
detail: Verified: `_parse_and_validate('{"recommendation":"r","cases":[{"option":"a","argument":"b"}],
  "tradeoffs":[""]}')` returns successfully with `tradeoffs == ['']`. Pydantic's `min_length` on a list
  constrains the collection length, not each element, so a model that emits a single empty-string
  trade-off passes schema validation and persists an empty trade-off. `cases` does NOT have this gap
  (each Case.option/argument has its own min_length=1). Low impact: the judge is prompted for non-empty
  trade-offs and this is within the plan's stated shape (list[str], min_length=1).
suggestion: Optional — constrain elements too, e.g. `tradeoffs: list[Annotated[str, Field(min_length=1)]]
  = Field(..., min_length=1, ...)`, so an empty-string trade-off triggers the repair path like other
  malformed output. Not required for KAN-8 acceptance.
```

```
severity: low
file: backend/app/services/judge.py
line: 58
issue: Single-line ```json fence (opening fence and JSON on the same line, no newline) is not stripped and forces a needless repair.
detail: _parse_and_validate strips a fence only by splitting on the first newline: when the opening ```
  and the JSON body share one line (no "\n"), the branch sets `stripped = ""`, so json.loads raises and
  a valid-but-single-line-fenced verdict costs one repair round-trip (an extra Opus call). Verified:
  a well-formed verdict wrapped as ```json {…}``` on one line → JSONDecodeError. The common multi-line
  fence case IS handled correctly (verified). Low impact: models overwhelmingly emit the multi-line form,
  and the repair path recovers it — this only costs latency/tokens, never correctness.
suggestion: Optional hardening — after dropping the language tag, also strip a leading/trailing ``` run
  on the same line, or use a small regex like `^```[a-zA-Z]*\s*` / `\s*```$`. Not required for acceptance.
```

---

## Category-by-category

**1. Logic / correctness — clean.**
- Exactly-one-repair invariant (DEC-007) holds: `judge()` calls `complete` at most twice; the second
  failure raises `JudgeSchemaError` with no third call. Directly asserted by `test_malformed_twice_raises`
  (`len(stub.calls) == 2`) and the two one-repair tests.
- The `except … as err` unbinding hazard flagged in the brief is handled correctly: `judge()` captures
  `first_error = str(err)` INSIDE the except block (line 87) before falling through, then uses
  `first_error` to build the repair message — it never reads `err` after the block. In `llm.py.complete`,
  `last_error` is bound before the loop and the final `raise last_error` is unreachable (guarded by the
  in-loop `raise`), correctly commented `# pragma: no cover`.
- Fence-stripping multi-line and bare-JSON paths verified working; malformed input correctly raises
  `JSONDecodeError`/`ValidationError`, both caught identically by the repair caller. (Single-line-fence
  edge is the low nit above.)
- Repair conversation is a valid multi-turn `user → assistant(first) → user(repair)` (not a trailing
  prefill) — correct.
- `judge()` is kept pure (no session/persistence); `persist_verdict` is the separate seam for KAN-9.
  Matches the plan and the KAN-9 composition seam.

**2. Security — clean (no critical).** The judge prompt interpolates `debate.decision`, `debate.context`,
and transcript content (user- and model-originated) into an Opus prompt. This is inherent to an
LLM-judge feature and the realistic blast radius is minimal: the judge performs no tool use / code
execution, output is schema-validated before persistence, and a successful injection could at worst
skew the synthesized verdict (a product-trust concern, not a system-security one). No secrets are
logged (only token counts / model id / debate id). No injection into SQL (repo uses the ORM). Not
flagged as a finding — over-flagging per the brief's guidance.

**3. Performance — clean.** `_render_transcript` builds the `persona_id → name` map once (O(personas))
then does O(1) dict lookups per turn — no N+1. `repo.get_debate` eager-loads personas/turns/verdict
via `selectinload`, so `debate.personas`/`.turns` don't lazy-fire; the orchestrator already returns a
reloaded, eager-loaded debate for the judge to consume. The judge is a single non-streaming Opus call
(+ at most one repair) — appropriate.

**4. Code quality — clean.** `complete` mirrors `run_turn`'s retry/backoff/text-extraction idiom
(`"".join(b.text for b in response.content if b.type == "text")`), the injectable `self._sleep`, and
the cost log line — but correctly diverges by carrying no guardrails/persona plumbing and re-raising
instead of degrading to skipped. Naming, type hints, and DEC-citing module docstrings are consistent
with `services/personas.py` / `prompts/personas.py`. Model id resolved via `model_for("judge")`, never
hardcoded (DEC-007).

**5. Codebase standards — clean.** ruff check + format clean at line-length 100; module-level logger;
Pydantic v2 (`Field(..., min_length=…, description=…)`, `model_validate`, `model_dump`); correct
schema/prompt/service split; tests use the `StubJudgeLLM` injection pattern and the in-memory `session`
fixture consistent with `test_orchestrator`/`test_repository`.

**6. Architecture-doc & decision drift.** No Accepted DEC is contradicted; the change REALIZES DEC-007
and introduces no undocumented decision (the prompt-constrained-JSON vs native-structured-output
trade-off is explicitly within DEC-007 per the plan's design note). The `Verdict` schema shape matches
`models/verdict.py` exactly (`recommendation: str`, `cases: [{option, argument}]`, `tradeoffs: list[str]`)
and `persist_verdict` maps `[c.model_dump() for c in cases]` onto `repo.set_verdict`, matching the JSON
shape `test_repository` asserts. **Doc-drift verdict: doc update NEEDED at /commit** (see the first
finding) — not accurate as-is, but the staleness is the expected pre-commit state the plan defers to
`/commit`.

## Acceptance criteria (KAN-8) — all met
Opus-tier judge ✅ · schema-validated Verdict ✅ · exactly-one repair then `JudgeSchemaError` ✅ ·
`persist_verdict` via `repo.set_verdict`, linkable to transcript ✅ · valid/one-repair/raise tests ✅ ·
ruff + full pytest green, no regressions ✅ · conventions followed ✅ · `judge()` kept pure ✅.
