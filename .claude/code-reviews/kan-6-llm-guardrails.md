# Code Review — KAN-6 (TICKET-3) LLM turn client, model tiers & cost guardrails

**Reviewed:** 2026-08-29 · **Branch:** KAN-6 · **Governing decision:** DEC-007

**Stats:**
- Files Modified: 2 (`app/core/config.py`, `app/services/llm.py`)
- Files Added: 3 (`app/core/guardrails.py`, `tests/test_guardrails.py`, `tests/test_llm.py`)
- Files Deleted: 0

## Scope
A guarded LLM turn client: model-tier routing (DEC-007), hard cost caps enforced before every
call, per-turn retry-with-backoff that degrades to a skipped turn instead of raising, and
per-call cost/latency logging. Decoupled from persistence (no TICKET-1 DB imports).

## Findings

### Logic errors
None. Cap checks use correct boundaries (`persona_index >= max_personas`, `round_number >
max_rounds`, projected-token `>` cap). Retry loop runs `max_retries + 1` attempts and only
sleeps between attempts, not after the last. Usage is recorded only on the successful attempt.

### Security
None. No secrets logged (only tiers, model ids, token counts, latency). API key still sourced
from settings/env. No network calls in tests (Anthropic client mocked).

### Performance
Backoff `sleep` is injectable (`LLMService(sleep=...)`), so tests run instantly; production uses
`time.sleep`. Guardrails are O(1) checks.

### Code quality
- Full type hints; `Tier` literal; `TurnResult` is a small pydantic model with no persistence
  coupling. Matches existing service/singleton conventions; `chat()` and `get_llm_service()`
  left intact so the chat route and `test_health` keep working.
- `ruff check` and `ruff format --check` clean.

### Adherence to decisions
- DEC-007 model ids exact: `claude-sonnet-5` / `claude-opus-4-8` / `claude-haiku-4-5-20251001`.
- No new architectural decision introduced; no contradiction with any Accepted DEC.

## Judgment calls (reasonable, documented in code)
- Token cap is a **projection** (`tokens_spent + max_tokens`) so a call that would breach the cap
  is blocked before spending.
- "Transient" = any `anthropic.APIError`; cleanly distinct from `CapExceededError`.

## Verification
- `uv run pytest -q` → 13 passed (11 new across guardrails+llm, 2 pre-existing).
- `uv run ruff check app tests` → All checks passed.
- `uv run ruff format --check` → clean.
- Each AC maps to a test: tier selection (`test_model_tier_selection_from_config`,
  `test_successful_turn_uses_tier_model_and_records_usage`), pre-call block
  (`test_cap_exceeded_blocks_call_and_returns_skipped` asserts `create` never called),
  retry→ok (`test_transient_error_is_retried_then_succeeds`), persistent→skipped
  (`test_persistent_error_returns_skipped_after_retries`), guardrail units (`test_guardrails.py`).

## Result
Code review passed. No technical issues detected.
