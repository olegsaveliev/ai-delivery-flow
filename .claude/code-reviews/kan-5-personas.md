# Code Review — KAN-5 (TICKET-2) Persona archetypes & stance framing

**Reviewed:** 2026-08-29 · **Branch:** KAN-5 · **Governing decisions:** DEC-001, DEC-002

**Stats:**
- Files Modified: 0
- Files Added: 5 (`app/prompts/__init__.py`, `app/prompts/personas.py`, `app/schemas/persona.py`, `app/services/personas.py`, `tests/test_personas.py`)
- Files Deleted: 0
- New lines: ~200
- Deleted lines: 0

## Scope
New pure/deterministic persona-assignment layer. No persistence, no orchestration, no HTTP,
no LLM call — consistent with the ticket's stated scope.

## Findings

### Logic errors
None. `assign_personas` strips and rejects empty/whitespace `decision` (`ValueError`);
fixed tuple `ARCHETYPES` guarantees exactly 3 personas in a deterministic order.

### Security
None. `ArchetypeSpec.frame_stance` calls `stance_template.format(decision=decision)` where the
format string is a controlled module constant and the user-supplied `decision` is only the
substituted *value* — it is never parsed as a format string, so a `decision` containing
`{...}` cannot trigger format-string injection or attribute traversal.

### Performance
N/A — constant-size, in-memory work.

### Code quality
- Full type annotations; `ruff check` and `ruff format` clean.
- `context` parameter is accepted but unused; intentionally documented as reserved for a stable
  call contract / downstream turn seeding. Not a defect.
- System prompts kept out of the serializable `Persona` payload (looked up by archetype via
  `system_prompt_for`) — clean separation between the `personas_assigned` payload and the
  orchestrator's internal prompt.

### Adherence to codebase standards & decisions
- Matches the frozen Persona contract from the epic spec (persistence-assigned `id`/`debate_id`
  intentionally absent for the in-memory return).
- DEC-001 (fixed archetypes + per-decision stance) and DEC-002 (exactly 3) honored.
- Test style mirrors `tests/test_health.py`.

## Verification
- `uv run pytest -q` → 14 passed (12 new + 2 existing).
- `uv run ruff check app tests` → All checks passed.
- `uv run ruff format --check` → clean.

## Result
Code review passed. No technical issues detected.
