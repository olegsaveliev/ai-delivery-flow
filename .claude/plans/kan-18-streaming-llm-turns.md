# Feature: KAN-18 (EPIC-B · B-T2) — Streaming persona turns in the LLM client

The following plan should be complete, but it's important that you validate documentation and
codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files.

## Feature Description

Add `LLMService.stream_turn(..., on_delta)` — a streaming sibling of `run_turn` that calls the
Anthropic **streaming** Messages API on the persona tier (`claude-sonnet-5`, DEC-007) and pushes
each text delta to a caller-supplied synchronous callback as it is generated. It keeps every
`run_turn` guarantee (guardrails checked before the call, retry with backoff, cost/latency logged,
usage recorded to `DebateGuardrails`, never raises → degrades to a `skipped` `TurnResult`) and adds
two streaming-specific rules:

1. **Retry only before the first delta.** Once any delta has reached `on_delta`, a failure is
   terminal for that turn (`skipped`, reason recorded) so no client ever sees duplicated text.
2. **Per-turn wall-clock timeout** (`turn_timeout_seconds` in config) → `skipped` with reason
   `"timeout"`.

This is the LLM-client half of EPIC-B live streaming. The orchestrator (KAN-19 / B-T3) will later
call it from worker threads and bridge deltas onto the event loop; this ticket does **not** touch
the orchestrator, events, SSE, or any endpoint.

## User Story

As a viewer
I want each persona's words to arrive as they are generated
So that the debate feels alive rather than appearing in blocks.

## Problem Statement

`LLMService.run_turn` is request/response: a persona turn is invisible until the full completion
returns (often 10–20 s). EPIC-B needs token-level deltas to stream to the browser, but must not
regress the cost guardrails (PRD §8/§9), the skip-tolerance contract the orchestrator relies on,
or introduce duplicated text on retries.

## Solution Statement

Add a new public method `stream_turn` on `LLMService` (and a private single-attempt helper
`_stream_attempt`) that:

1. Resolves the tier model (`model_for`, default tier `"personas"`), runs `guardrails.check(...)`
   exactly like `run_turn`; a `CapExceededError` returns `skipped` with `reason="guardrail: ..."`
   and makes **no** API call and **no** `on_delta` call.
2. Sets a wall-clock deadline `self._clock() + turn_timeout_seconds` covering **all** attempts.
3. For each attempt: opens `self._client.messages.stream(model=..., max_tokens=..., messages=...,
   [system=...], timeout=turn_timeout_seconds)`, iterates `stream.text_stream`, and for each delta
   checks the deadline, then calls `on_delta(delta)` and appends to a local buffer.
4. On clean completion: reads usage from `stream.get_final_message().usage`, logs exactly like
   `run_turn` (`turn ok: ... tokens_in tokens_out latency_ms`), calls
   `guardrails.record_usage(...)`, and returns `ok` with `text = "".join(deltas)`.
5. Classifies failures:
   - **Timeout** (`APITimeoutError`, or *any* exception / delta observed once the deadline has
     passed) → `skipped`, `reason="timeout"`, **no retry** (before or after the first delta).
   - **Any other exception before the first delta** → transient; retry with
     `self._backoff_base * 2**attempt` via `self._sleep`, up to `llm_max_retries`; exhausted →
     `skipped`, `reason="llm error after N attempts: ..."` (same wording as `run_turn`).
   - **Any exception after the first delta** (API error event, transport drop, or `on_delta`
     itself raising) → `skipped`, `reason="stream interrupted after K deltas: ..."`, **no retry**.
6. Never raises. `run_turn`, `complete`, and `chat` are **not modified**.

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: Medium (small surface, but failure classification + timeout semantics
need care and precise tests)
**Primary Systems Affected**: `backend/app/services/llm.py`, `backend/app/core/config.py`,
`backend/tests/test_llm.py`. `docs/architecture.md` (+ Confluence 917506) at commit time (DEC-011).
**Dependencies**: `anthropic` (installed **1.2.0**, uses `httpx2` transport) — no new deps.

**Governing decisions (cite in the commit trailer: `Decisions: DEC-007`):**
- **DEC-007** — persona tier = `claude-sonnet-5`. `stream_turn` resolves the model via
  `self.model_for(tier)` with default `tier="personas"`; never hardcode the model id.
- **PRD §8/§9 cost guardrails** (`app/core/guardrails.py`) — hard caps checked *before* the call;
  token usage recorded after. Honored unchanged.
- **DEC-003** (concurrent turns) — informs the threading contract (called concurrently from
  worker threads); nothing in this ticket changes orchestration.
- **DEC-012** is *Proposed* and gates B-T4/B-T5 only — **not** a dependency of this ticket.
- No Accepted DEC is contradicted. The "retry only before first delta" policy is the story's AC
  (epic spec "Gaps and assumptions"), not a new architectural decision — see NOTES.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — READ THESE BEFORE IMPLEMENTING

- `backend/app/services/llm.py` (whole file, 242 lines)
  - lines 18–32 `TurnResult` — reuse as-is (`status`, `model`, `text`, `tokens_in`, `tokens_out`,
    `latency_ms`, `reason`). Do **not** add fields.
  - lines 38–50 `__init__` — add `clock` injectable next to `sleep`, and `self._turn_timeout`.
  - lines 65–171 `run_turn` — **the structure to mirror** (guardrail block 85–100, retry loop
    104–128, usage extraction 132–134, success log 137–147, `record_usage` 148, exhausted-skip
    158–171). **Do not edit a single line of it.**
- `backend/app/core/config.py` (lines 28–30) — resilience settings block; add
  `turn_timeout_seconds` there.
- `backend/app/core/guardrails.py` (lines 36–60) — `check(round_number, persona_index,
  estimated_tokens)` raises `CapExceededError`; `record_usage(tokens_in, tokens_out)`.
- `backend/app/services/orchestrator.py` (lines 11–16, 134–145) — how `run_turn` is invoked via
  `asyncio.to_thread` with keyword args; `stream_turn` must accept the same keywords plus
  `on_delta` so KAN-19 can swap it in. **Read only — do not change.**
- `backend/tests/test_llm.py` (whole file) — helpers `make_service`, `make_response`,
  `make_guardrails`, `transient_error`, `MESSAGES`. Existing tests must pass **unchanged**.
- `backend/.env.example` — documents only `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `CORS_ORIGINS`,
  `DATABASE_URL`; resilience/guardrail knobs (`LLM_MAX_RETRIES`, `MAX_TOKENS_PER_DEBATE`, …) are
  **not** documented there, so no change is required (see NOTES).
- `docs/architecture.md` (line 44 component table row for `app/services/llm.py`; Change Log at
  ~line 165) — updated by `/commit` (DEC-011), not during implementation.

### SDK facts verified against the installed `anthropic==1.2.0`

Source: `backend/.venv/lib/python3.14/site-packages/anthropic/`.

- `client.messages.stream(*, max_tokens, messages, model, system=..., timeout=..., ...)` returns a
  `MessageStreamManager` (`resources/messages/messages.py:1000`). It accepts
  `timeout: float | httpx2.Timeout`.
- **The HTTP request is sent in `MessageStreamManager.__enter__`** (`lib/streaming/_messages.py:
  170–173`). So HTTP-status failures (429/500/529 → `RateLimitError`/`InternalServerError`/
  `OverloadedError`, all `APIStatusError ⊂ APIError`) and connection failures
  (`APIConnectionError`, `APITimeoutError ⊂ APIConnectionError ⊂ APIError`) raise from the `with`
  statement — always **before** any delta. The SDK's own client-level retries
  (`DEFAULT_MAX_RETRIES = 2`) also apply to this initial request, exactly as they already do for
  `run_turn`'s `messages.create`.
- `MessageStream.text_stream` (`_messages.py:144–147`) yields `chunk.delta.text` for every
  `content_block_delta` / `text_delta` event.
- **Mid-stream `error` SSE event** (e.g. `overloaded_error` after some deltas) raises from
  iteration as `APIStatusError` built by `_client._make_status_error` using the *200* response
  status → a plain `APIStatusError` (`_streaming.py:131–144`, `_client.py:507–543`).
- **Mid-stream transport failures are NOT wrapped**: body reads go through
  `response.iter_bytes()` with no try/except, so a stalled read raises raw `httpx2.ReadTimeout`
  and a dropped connection raises raw `httpx2.RemoteProtocolError` / `httpx2.ReadError` — these
  are **not** `APIError` subclasses. Hence `stream_turn` must catch `Exception` (not just
  `APIError`) around iteration to honor "never raises".
- `get_final_message()` (`_messages.py:93–99`) consumes any remainder and returns the accumulated
  `Message`; `.usage.input_tokens` / `.usage.output_tokens` are populated from `message_start` /
  `message_delta`.
- Exiting the `with` block calls `stream.close()` → releases the connection (also on break /
  exception). Always use the context manager.
- httpx `timeout=` is **per operation** (connect / each read), not wall-clock: a slow-but-steady
  stream never trips it. Hence the explicit monotonic deadline.

### New Files to Create

None. All changes are in existing files.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- Anthropic streaming Messages — https://docs.anthropic.com/en/api/messages-streaming
  - Sections: "Streaming with SDKs" (Python `with client.messages.stream(...) as stream:` /
    `stream.text_stream`), "Event types", "Error events" (errors such as `overloaded_error` can
    arrive mid-stream after a 200).
  - Why: confirms event model and that mid-stream errors are possible (the core of AC #3).
- Anthropic Python SDK README — https://github.com/anthropics/anthropic-sdk-python#streaming-helpers
  and #timeouts / #retries
  - Why: `timeout=` semantics and built-in retries on the initial request.
- The installed SDK source (paths above) is the authority where docs and source disagree.

### Patterns to Follow

**Naming / style:** keyword-only args (`*,`), `Tier` literal, snake_case, docstrings that state
guarantees; `logger = logging.getLogger(__name__)` with `%s`-style lazy args; line length 100.

**Guardrail block — copy from `run_turn` lines 85–100 verbatim (only the log prefix changes):**
```python
try:
    guardrails.check(
        round_number=round_number,
        persona_index=persona_index,
        estimated_tokens=max_tokens,
    )
except CapExceededError as exc:
    logger.warning(
        "stream turn blocked by guardrail: tier=%s round=%s persona=%s reason=%s",
        tier, round_number, persona_index, exc,
    )
    return TurnResult(status="skipped", model=model, reason=f"guardrail: {exc}")
```

**Usage extraction — mirror `run_turn` lines 132–134 (defensive `getattr ... or 0`):**
```python
usage = getattr(final_message, "usage", None)
tokens_in = getattr(usage, "input_tokens", 0) or 0
tokens_out = getattr(usage, "output_tokens", 0) or 0
```

**Success log — same message shape as `run_turn` (lines 137–147)** so log-based cost tooling
(EPIC-D) treats both identically; use prefix `"stream turn ok: "` and identical fields
`tier model round persona tokens_in tokens_out latency_ms`.

**Backoff:** `self._sleep(self._backoff_base * (2**attempt))` only when `attempt < self._max_retries`.

**Injectables:** `sleep` already injectable; add `clock: Callable[[], float] = time.monotonic`
the same way so tests never wait on real time.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation
Add `turn_timeout_seconds` to `Settings`; add `clock` + `_turn_timeout` to `LLMService.__init__`.

### Phase 2: Core Implementation
Add `stream_turn` (guardrails → deadline → attempt loop → exhausted skip) and `_stream_attempt`
(one stream: iterate, deadline check, emit, classify failures), plus a private sentinel exception
`_RetryableStreamError` used only to signal "failed before first delta, retry me".

### Phase 3: Integration
None in code (orchestrator wiring is KAN-19). Document the threading/callback contract in the
docstring. Architecture doc + Confluence mirror updated at `/commit` time.

### Phase 4: Testing & Validation
Add a fake stream harness and 12 focused tests to `test_llm.py`; run full backend suite + ruff.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### UPDATE `backend/app/core/config.py`

- **ADD** under the "Per-turn resilience" block (after `llm_backoff_base_seconds`, line 30):
  ```python
  # Per-turn wall-clock budget for a streamed persona turn (EPIC-B, KAN-18). Spans all
  # retry attempts; exceeding it yields a skipped turn with reason "timeout".
  turn_timeout_seconds: float = 60.0
  ```
- **GOTCHA**: env var is `TURN_TIMEOUT_SECONDS` (pydantic-settings, case-insensitive). 60 s is
  comfortably above a 1024-token Sonnet turn (~10–25 s) while bounding a hung stream. KAN-17
  (parallel) may also touch this file — keep the addition self-contained to make the merge
  trivial.
- **VALIDATE**: `cd backend && python -c "from app.core.config import Settings; print(Settings().turn_timeout_seconds)"` → `60.0`

### UPDATE `backend/app/services/llm.py` — `__init__`

- **IMPLEMENT**: change the signature to
  `def __init__(self, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:`
  (wrap to ≤100 cols) and add:
  ```python
  self._turn_timeout = settings.turn_timeout_seconds
  # Injectable monotonic clock so tests can drive the per-turn deadline deterministically.
  self._clock = clock
  ```
- **GOTCHA**: keyword default keeps `LLMService(sleep=...)` and `LLMService()` call sites working
  (`make_service`, `get_llm_service`). Do not touch any other existing line.
- **IMPORTS**: add `APITimeoutError` to `from anthropic import Anthropic, APIError` →
  `from anthropic import Anthropic, APIError, APITimeoutError`.
- **VALIDATE**: `cd backend && python -m pytest tests/test_llm.py -q` (existing 7 tests green).

### ADD `_RetryableStreamError` (module-private) in `backend/app/services/llm.py`

- **IMPLEMENT** (above `class LLMService`):
  ```python
  class _RetryableStreamError(Exception):
      """Internal signal: a stream attempt failed before emitting any delta (safe to retry)."""
  ```
  Raised by `_stream_attempt` with `raise _RetryableStreamError(str(exc)) from exc`; caught only
  by `stream_turn`. Never escapes the class.
- **VALIDATE**: `cd backend && ruff check app/services/llm.py`

### ADD `LLMService.stream_turn` in `backend/app/services/llm.py`

Place it directly after `run_turn` (before `complete`).

- **SIGNATURE**:
  ```python
  def stream_turn(
      self,
      *,
      on_delta: Callable[[str], None],
      messages: list[ChatMessage],
      guardrails: DebateGuardrails,
      round_number: int,
      persona_index: int,
      tier: Tier = "personas",
      max_tokens: int = 1024,
      system: str | None = None,
  ) -> TurnResult:
  ```
  Keyword names match `run_turn` exactly (so KAN-19 can pass the same kwargs + `on_delta`);
  `tier` defaults to `"personas"` per the AC ("on the persona tier", DEC-007).
- **DOCSTRING** must state the contract:
  - Synchronous and blocking; designed to run in a worker thread (`asyncio.to_thread`).
  - `on_delta` is a plain **sync** callable invoked on the calling (worker) thread, once per text
    delta, in generation order; it must be fast and must not block (KAN-19 bridges it onto the
    event loop with `loop.call_soon_threadsafe`). It must not assume it is on the event loop.
  - Never raises. Returned `TurnResult.text == "".join(all deltas passed to on_delta)` when
    `status == "ok"`. On `skipped`, `text == ""` even if some deltas were already emitted — the
    consumer must discard partial text for that turn (turn_completed{skipped} in KAN-17/19).
  - Retry only before the first delta; timeout (`reason == "timeout"`) is never retried.
  - Safe to call concurrently for different personas; shares the `guardrails` object exactly as
    concurrent `run_turn` calls do today.
- **IMPLEMENT** (pseudocode — keep structure, names, and messages):
  ```python
  model = self.model_for(tier)
  # (a) guardrail block — copied from run_turn (see Patterns), log prefix "stream turn blocked"
  payload = [{"role": m.role, "content": m.content} for m in messages]
  deadline = self._clock() + self._turn_timeout

  last_error: Exception | None = None
  for attempt in range(self._max_retries + 1):
      if self._clock() >= deadline:
          return self._timeout_result(model, tier, round_number, persona_index, deltas=0)
      try:
          return self._stream_attempt(
              model=model, tier=tier, payload=payload, max_tokens=max_tokens, system=system,
              on_delta=on_delta, guardrails=guardrails, round_number=round_number,
              persona_index=persona_index, deadline=deadline,
          )
      except _RetryableStreamError as exc:
          last_error = exc.__cause__ or exc
          logger.warning(
              "stream turn attempt failed before first delta: tier=%s round=%s persona=%s "
              "attempt=%s/%s error=%s",
              tier, round_number, persona_index, attempt + 1, self._max_retries + 1, last_error,
          )
          if attempt < self._max_retries:
              self._sleep(self._backoff_base * (2**attempt))

  logger.error(  # same shape as run_turn lines 159–166
      "stream turn skipped after %s attempts: tier=%s round=%s persona=%s error=%s", ...
  )
  return TurnResult(
      status="skipped", model=model,
      reason=f"llm error after {self._max_retries + 1} attempts: {last_error}",
  )
  ```
  A tiny private `_timeout_result(...)` helper (logs a warning
  `"stream turn timed out: tier=%s round=%s persona=%s deltas=%s timeout_s=%s"` and returns
  `TurnResult(status="skipped", model=model, reason="timeout")`) avoids repeating that block in
  three places. `reason` must be exactly the string `"timeout"`.
- **GOTCHA**:
  - Deadline is computed **once**, before the first attempt, and spans retries + backoff sleeps.
  - Do not refactor `run_turn` to share code — AC says `run_turn` is unchanged; duplication of
    the ~15-line guardrail block is the accepted cost.
- **VALIDATE**: `cd backend && ruff check app/services/llm.py && python -c "from app.services.llm import LLMService; print(LLMService.stream_turn.__doc__[:40])"`

### ADD `LLMService._stream_attempt` in `backend/app/services/llm.py`

- **SIGNATURE**: keyword-only `(self, *, model, tier, payload, max_tokens, system, on_delta,
  guardrails, round_number, persona_index, deadline) -> TurnResult`. Returns a *terminal*
  `TurnResult` (ok / timeout / mid-stream skip) or raises `_RetryableStreamError`.
- **IMPLEMENT**:
  ```python
  chunks: list[str] = []
  emitted = False
  start = time.perf_counter()
  try:
      with self._client.messages.stream(
          model=model,
          max_tokens=max_tokens,
          messages=payload,
          timeout=self._turn_timeout,
          **({"system": system} if system is not None else {}),
      ) as stream:
          for delta in stream.text_stream:
              if self._clock() >= deadline:
                  return self._timeout_result(..., deltas=len(chunks))  # exits `with` → close()
              emitted = True          # set BEFORE the call: if on_delta raises, text may be out
              on_delta(delta)
              chunks.append(delta)
          final_message = stream.get_final_message()
  except Exception as exc:  # noqa: BLE001 — never-raises contract; SDK leaks raw httpx2 errors
      if isinstance(exc, APITimeoutError) or self._clock() >= deadline:
          return self._timeout_result(..., deltas=len(chunks))
      if not emitted:
          raise _RetryableStreamError(str(exc)) from exc
      logger.warning(
          "stream turn interrupted after first delta (not retried): tier=%s round=%s "
          "persona=%s deltas=%s error=%s", tier, round_number, persona_index, len(chunks), exc,
      )
      return TurnResult(
          status="skipped", model=model,
          reason=f"stream interrupted after {len(chunks)} deltas: {exc}",
      )

  latency_ms = (time.perf_counter() - start) * 1000
  usage = getattr(final_message, "usage", None)
  tokens_in = getattr(usage, "input_tokens", 0) or 0
  tokens_out = getattr(usage, "output_tokens", 0) or 0
  logger.info(
      "stream turn ok: tier=%s model=%s round=%s persona=%s "
      "tokens_in=%s tokens_out=%s latency_ms=%.1f",
      tier, model, round_number, persona_index, tokens_in, tokens_out, latency_ms,
  )
  guardrails.record_usage(tokens_in, tokens_out)
  return TurnResult(
      status="ok", model=model, text="".join(chunks),
      tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
  )
  ```
- **GOTCHA**:
  - **Timeout classification without importing `httpx2`:** the request `timeout=` equals
    `turn_timeout_seconds`, so any transport-level read/connect timeout can only fire after at
    least that long, i.e. after the wall-clock deadline. Checking `self._clock() >= deadline` in
    the `except` therefore catches raw `httpx2.ReadTimeout` too, without coupling to `httpx2`
    (a transitive dep; `pyproject.toml` pins only `anthropic>=0.40`, whose older versions used
    `httpx`). `APITimeoutError` covers a connect timeout raised from `__enter__`.
  - **Wall-clock bound:** the deadline is checked when each delta arrives, so a stream that stalls
    right before the deadline is bounded by the read timeout → worst case ≈ 2 ×
    `turn_timeout_seconds`. Acceptable for MVP; B-T5 adds the per-debate timeout on top. Note it
    in the docstring.
  - Check the deadline **before** calling `on_delta`, so no delta is emitted after timeout.
  - `return` inside the `with` is intentional: it triggers `__exit__` → `stream.close()`, freeing
    the connection on timeout.
  - `ruff` config does not enable `BLE` today; keep the `noqa` comment only if ruff flags it
    (otherwise drop it to avoid an unused-noqa `RUF100` warning — check `ruff check` output).
  - On a timeout or mid-stream skip we do **not** call `record_usage` (the final `usage` is not
    available). This slightly under-counts spend for aborted turns; bounded by `max_tokens` per
    turn. See NOTES / open question.
  - An empty-text but successful stream (no deltas) returns `ok` with `text=""` — same as
    `run_turn` when the model returns no text blocks.
- **VALIDATE**: `cd backend && ruff check . && python -m pytest tests/test_llm.py -q`

### UPDATE `backend/tests/test_llm.py` — fake streaming harness

Append (do **not** edit existing tests or helpers):

- **IMPLEMENT** helpers:
  ```python
  class FakeClock:
      """Manually advanced monotonic clock."""
      def __init__(self, now: float = 0.0) -> None:
          self.now = now
      def __call__(self) -> float:
          return self.now

  class FakeStream:
      """Stand-in for anthropic's MessageStream (what `with ...stream() as s` yields)."""
      def __init__(self, deltas, *, error=None, tokens_in=12, tokens_out=34, clock=None,
                   advance_after=None, advance_by=0.0):
          self._deltas, self._error = list(deltas), error
          self._clock, self._advance_after, self._advance_by = clock, advance_after, advance_by
          self._usage = SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out)
          self.closed = False
      @property
      def text_stream(self):
          for i, d in enumerate(self._deltas):
              if self._clock is not None and self._advance_after == i:
                  self._clock.now += self._advance_by   # simulate slow generation
              yield d
          if self._error is not None:
              raise self._error                        # mid-stream (or pre-delta if no deltas)
      def get_final_message(self):
          return SimpleNamespace(usage=self._usage)

  class FakeStreamManager:
      """Context manager returned by client.messages.stream(...)."""
      def __init__(self, stream=None, *, enter_error=None):
          self._stream, self._enter_error = stream, enter_error
      def __enter__(self):
          if self._enter_error is not None:
              raise self._enter_error                  # HTTP-level failure: request sent in __enter__
          return self._stream
      def __exit__(self, *exc):
          if self._stream is not None:
              self._stream.closed = True
          return False

  def make_stream_service(clock=None) -> LLMService:
      service = LLMService(sleep=lambda _: None, clock=clock or FakeClock())
      service._client = MagicMock()
      return service

  def stream_kwargs(guard, on_delta, **overrides):
      kw = dict(on_delta=on_delta, messages=MESSAGES, guardrails=guard,
                round_number=1, persona_index=0)
      kw.update(overrides)
      return kw
  ```
  Wire fakes with `service._client.messages.stream.side_effect = [FakeStreamManager(...), ...]`
  (one manager per attempt) or `.return_value = FakeStreamManager(...)`.
- **IMPORTS**: add `APIStatusError`, `APITimeoutError` from `anthropic` only if used;
  `httpx` is **not** needed. For a mid-stream API error use `transient_error()` (existing helper,
  `APIConnectionError(request=None)`), and for a raw transport error use a plain
  `ConnectionResetError("dropped")` (proves non-`APIError` exceptions are handled). Build
  `APITimeoutError(request=None)` directly (constructor takes only `request`).
- **VALIDATE**: `cd backend && python -m pytest tests/test_llm.py -q`

### UPDATE `backend/tests/test_llm.py` — tests

Each test collects deltas with `received: list[str] = []` / `on_delta=received.append`.

1. `test_stream_turn_happy_path_emits_deltas_in_order`
   — deltas `["Take ", "the ", "job."]`, tokens 12/34 → `status == "ok"`,
   `received == ["Take ", "the ", "job."]`, `result.text == "".join(received)`,
   `tokens_in == 12`, `tokens_out == 34`, `guard.tokens_spent == 46`,
   `result.model == "claude-sonnet-5"`; `kwargs["model"] == "claude-sonnet-5"` (DEC-007),
   `kwargs["timeout"] == 60.0`, `"system" not in kwargs`; the `FakeStream.closed` is True.
2. `test_stream_turn_passes_system_prompt` — `system="be the skeptic"` → `kwargs["system"]`.
3. `test_stream_turn_guardrail_block_makes_no_call_and_no_deltas`
   — `persona_index=3` with `max_personas=3` → `skipped`, `"guardrail" in reason`,
   `received == []`, `service._client.messages.stream.assert_not_called()`.
4. `test_stream_turn_retries_transient_error_before_first_delta`
   — `side_effect=[FakeStreamManager(enter_error=transient_error()), FakeStreamManager(FakeStream(["hi"]))]`
   → `ok`, `text == "hi"`, `received == ["hi"]`, `stream.call_count == 2`,
   `guard.tokens_spent == 46`. Also assert sleep was called with `0.5` (inject a recording
   `sleep` via `LLMService(sleep=sleeps.append, clock=FakeClock())`).
5. `test_stream_turn_retries_error_raised_before_any_delta_during_iteration`
   — first stream `FakeStream([], error=ConnectionResetError("dropped"))` (fails while reading,
   before any text), second succeeds → `ok`, `stream.call_count == 2` (raw non-`APIError`
   pre-delta failures are retried too).
6. `test_stream_turn_persistent_pre_delta_failure_skips_after_retries`
   — every manager raises `transient_error()` on enter (`side_effect` list of 3) → `skipped`,
   `"llm error after 3 attempts" in reason`, `stream.call_count == 3`, `received == []`,
   `guard.tokens_spent == 0`.
7. `test_stream_turn_failure_mid_stream_is_not_retried_and_skips`
   — `FakeStream(["partial ", "text"], error=transient_error())` → `skipped`,
   `"stream interrupted after 2 deltas" in reason`, `received == ["partial ", "text"]`
   (emitted once, never duplicated), `result.text == ""`, `stream.call_count == 1`,
   `guard.tokens_spent == 0`.
8. `test_stream_turn_on_delta_exception_is_contained`
   — `on_delta` raises `RuntimeError("consumer bug")` on the first delta → returns `skipped`
   (does not raise), `"stream interrupted" in reason`, `stream.call_count == 1`.
9. `test_stream_turn_timeout_mid_stream_skips_with_reason_timeout`
   — `clock = FakeClock()`; `FakeStream(["a", "b", "c"], clock=clock, advance_after=1,
   advance_by=61.0)` → `skipped`, `reason == "timeout"`, `received == ["a"]` (no delta emitted
   after the deadline), `stream.call_count == 1`, `FakeStream.closed is True`,
   `guard.tokens_spent == 0`.
10. `test_stream_turn_timeout_before_first_delta_is_not_retried`
    — `side_effect=[FakeStreamManager(enter_error=APITimeoutError(request=None)), <success>]`
    → `skipped`, `reason == "timeout"`, `stream.call_count == 1`, `received == []`.
11. `test_stream_turn_raw_read_timeout_after_deadline_classified_as_timeout`
    — stream yields `"a"`, then (clock advanced by 61 before raising) raises
    `TimeoutError("read timed out")` (stand-in for raw `httpx2.ReadTimeout`) → `reason ==
    "timeout"` (not "stream interrupted"). Implement by `FakeStream(["a"], error=TimeoutError(...),
    clock=clock, advance_after=None)` and advancing the clock inside `on_delta`
    (`def on_delta(d): received.append(d); clock.now += 61`).
12. `test_run_turn_does_not_use_streaming` — call existing-style `run_turn` with
    `messages.create.return_value = make_response()` and assert
    `service._client.messages.stream.assert_not_called()` (guards "run_turn unchanged").

- **PATTERN**: existing tests in `test_llm.py` (lines 47–124): Arrange with `make_*`, Act one call,
  Assert on `TurnResult` + mock call counts + `guard.tokens_spent`.
- **GOTCHA**:
  - `MagicMock().messages.stream` is auto-created; always set `side_effect`/`return_value`
    explicitly, otherwise `with MagicMock()` yields a MagicMock whose `text_stream` is not
    iterable (TypeError → caught → misleading retries).
  - Test 9: the deadline is `0 + 60 = 60`; attempt-start check sees `0` (<60); delta "a" at 0 →
    emitted; before yielding "b" the fake advances to 61 → check trips → timeout. Verify the
    index semantics of `advance_after` match this (advance happens *before* yielding index 1).
  - Tests 1–11 use `make_stream_service` / `LLMService(..., clock=FakeClock())` so real time is
    never consulted for the deadline.
- **VALIDATE**: `cd backend && python -m pytest tests/test_llm.py -q -v`

---

## TESTING STRATEGY

### Unit Tests
All in `backend/tests/test_llm.py` with a mocked Anthropic client and hand-rolled fake stream
context manager (no network, no real sleep, no real clock). Covers every AC branch:
happy path (order + concatenation + usage + tier), guardrail block (no call, no deltas),
retry-before-first-delta (enter-time and iteration-time failures), exhausted retries,
failure-mid-stream → skipped not retried, consumer callback failure contained,
timeout mid-stream / before first delta / raw read timeout → `reason == "timeout"`, and a
`run_turn` non-regression check. Existing 7 tests pass unchanged.

### Integration Tests
None in this ticket. The real consumer (orchestrator + event bridge) is KAN-19, which will test
`stream_turn` behind a stub. A live smoke test against the API is optional (Level 4).

### Edge Cases
- Stream completes with zero deltas → `ok`, `text == ""`, usage still recorded.
- Failure raised by iteration before any text (e.g. after `message_start`) → still retryable.
- `on_delta` raises → contained, skipped, not retried (text may already be visible).
- Deadline passes during backoff between attempts → next attempt-start check returns `timeout`
  without calling the API again.
- Concurrent calls sharing one `DebateGuardrails` — unchanged semantics vs `run_turn`.

### E2E / Browser Automation
**N/A for this ticket.** No HTTP route or UI is added; streaming reaches a browser only after
KAN-19 (events) and B-T4 (endpoint, blocked on DEC-012). Skip Level 5.

---

## VALIDATION COMMANDS

Prefer the project's `/validate` skill, which wraps these.

### Level 1: Syntax & Style
```bash
cd backend && ruff check .
```

### Level 2: Unit Tests
```bash
cd backend && python -m pytest tests/test_llm.py -q -v
```

### Level 3: Full Backend Suite (no regressions)
```bash
cd backend && python -m pytest -q
```

### Level 4: Manual Validation (optional, needs a real `ANTHROPIC_API_KEY` in `backend/.env`)
```bash
cd backend && python -c "
from app.core.guardrails import guardrails_from_settings
from app.schemas.chat import ChatMessage
from app.services.llm import LLMService
r = LLMService().stream_turn(
    on_delta=lambda d: print(d, end='', flush=True),
    messages=[ChatMessage(role='user', content='In two sentences: take the job?')],
    guardrails=guardrails_from_settings(), round_number=1, persona_index=0, max_tokens=200)
print('\n', r.status, r.tokens_in, r.tokens_out, round(r.latency_ms), r.reason)
"
```
Expect text printed incrementally, then `ok <n> <n> <ms> None`. Then re-run with
`TURN_TIMEOUT_SECONDS=0.5` → expect `skipped ... timeout`.

### Level 5: E2E / Browser Automation
N/A (see Testing Strategy).

---

## ACCEPTANCE CRITERIA

- [ ] `LLMService.stream_turn(..., on_delta)` exists, uses `client.messages.stream` on the persona
      tier by default (`claude-sonnet-5`, DEC-007 via `model_for`).
- [ ] `on_delta(text)` is called once per text delta, in order; `TurnResult.text` equals the
      concatenation of the deltas on `ok`.
- [ ] Guardrail breach → `skipped` (`reason` starts with `guardrail:`), no API call, no deltas.
- [ ] Tokens in/out logged (`stream turn ok: ...` with the same fields as `run_turn`) and recorded
      via `guardrails.record_usage` on success.
- [ ] Transient failure before the first delta → retried with `backoff_base * 2**attempt`; exhausted →
      `skipped` `llm error after N attempts: ...`.
- [ ] Failure after ≥1 delta → not retried, `skipped` with `stream interrupted after K deltas: ...`.
- [ ] Per-turn timeout (`turn_timeout_seconds`, default 60) → `skipped` with `reason == "timeout"`,
      never retried, no delta emitted after the deadline.
- [ ] `stream_turn` never raises (incl. raw transport errors and a raising `on_delta`).
- [ ] `run_turn`, `complete`, `chat` source unchanged; all pre-existing tests pass unchanged.
- [ ] `ruff check .` clean; full `pytest` green.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task validation passed immediately
- [ ] `cd backend && ruff check . && python -m pytest -q` green
- [ ] `git diff backend/app/services/llm.py` shows no changes inside `run_turn` / `complete` / `chat`
- [ ] Level 5 E2E: N/A (headless; documented)
- [ ] Commit message carries trailer `Decisions: DEC-007` (commit-msg hook enforces a trailer)
- [ ] `/commit` (DEC-011): update `docs/architecture.md` line 44 row for `app/services/llm.py` to
      mention `stream_turn` (streamed persona turns, retry-before-first-delta, per-turn timeout,
      `KAN-18`), add a Change Log row (`KAN-18 · DEC-007`), and mirror both to Confluence 917506
- [ ] Decision Log: append `KAN-18 · <sha>` to DEC-007 "Implemented by" only if the team treats
      streaming persona turns as realizing DEC-007 routing (see open question); otherwise no change
- [ ] Jira KAN-18: link the commit / PR

---

## NOTES

> **Second amendment — re-review (2026-09-24). The watchdog is dropped.** The re-review
> (`.claude/code-reviews/kan-18-streaming-llm-turns-rereview.md`) reproduced two medium defects
> in the first-amendment design:
> - Connect timeouts were no longer retried.
> - The watchdog could `shutdown()` a pooled socket that had already been released to another
>   concurrent persona turn. It killed an innocent turn in a forced-interleaving probe.
>
> The coordinator decided to **drop the watchdog and simplify** rather than keep patching it.
> This supersedes the first amendment's decisions 7 (watchdog part), 9 and 13. As built now:
> - **No watchdog.** `_StreamWatchdog`, the timer factory and `timer` injectable, `_force_close`
>   and the socket shutdown are all gone.
> - **The budget is enforced per attempt and at every text delta, and nothing more.**
>   - Request timeout = `anthropic.Timeout(remaining, connect=min(5, remaining))`.
>   - The deadline is checked before each delta is emitted.
>   - Backoff is capped at the remaining budget.
>
>   A stall is cut off by the read timeout at about the remaining budget. **A stream that sends
>   only keepalive pings and no text can exceed the budget** (probe: 4.17 s against a 1 s
>   budget). It is bounded only by the per-debate timeout in B-T5, so B-T5 must be the outer
>   bound for ping-only stalls. Add that sentence to B-T5 in the spec when it is next edited.
> - **Timeout is classified by budget, not by type** (supersedes decision 9). A failure is
>   `timeout` only when `clock() >= deadline` and it is not an `APIStatusError`.
>   `_exception_chain`/`_is_timeout_error` are removed. An `APITimeoutError` or connect timeout
>   with budget left is transient and retried (probe: connect timeout, then ok, in 2 requests).
>   A 5xx/429/overloaded error past the deadline keeps its real reason.
> - **Usage.** A retried attempt charges its snapshot usage before it retries (it was dropped
>   before), and the skip paths keep the best-effort snapshot read. Output tokens on an aborted
>   stream may be under-counted, because they only update on `message_delta`. There is no
>   estimate from characters.
> - **`x-should-retry`** (`true`/`false`) is honored before the status rule. `retry-after` is
>   not honored; this is documented in the docstring.
> - **Simplifications:**
>   - `import httpx2` directly, no `httpx` fallback; `pyproject.toml` pins `anthropic>=1.2`
>     (installed 1.2.0).
>   - The retry-disabled client is built once in `__init__` as
>     `self._stream_client = self._client.with_options(max_retries=0)`, sharing the HTTP pool.
>   - The duplicate deadline branch in `_stream_attempt` is gone.
>   - A frozen `_TurnCtx(tier, model, round_number, persona_index)` replaces the keyword-argument
>     threading.
>   - Watchdog-only tests and the two duplicate tests the re-review named are removed.
> - `llm.py`: 764 → 648 lines (`main`: 242).

> **Post-review amendment (2026-09-24), superseded in part by the second amendment above.** The KAN-18 code review
> (`.claude/code-reviews/kan-18-streaming-llm-turns.md`) reproduced timeout, retry and cost
> defects with probes against a local fake SSE server. The user approved "fix all". Where the
> review conflicts with this plan, **the review wins**: NOTES 2, 4, 7 and 8 and the GOTCHA
> "any transport timeout implies the deadline passed / clock past deadline means timeout" are
> **superseded** by the notes below. The pseudocode in STEP-BY-STEP TASKS (`timeout=self._turn_timeout`,
> clock-based timeout classification, "no `record_usage` on skip") shows the pre-review design;
> the code in `backend/app/services/llm.py` is authoritative.

**Design decisions (as built):**

1. **Timeout is terminal, never retried — even before the first delta.** The AC says "per-turn
   timeout → skipped with reason `timeout`", and the budget is *per turn*: one wall-clock deadline
   spans every attempt and backoff sleep. A *transient* non-timeout pre-delta failure is still
   retried (AC #3). *(unchanged)*
2. **Retry only transient pre-delta failures** *(supersedes the old NOTE 2)*. Retried:
   `APIConnectionError`; `APIStatusError` with status 408/409/429/5xx; a mid-stream `error` event
   whose body type is `overloaded_error` / `api_error` / `rate_limit_error` / `timeout_error` (these
   arrive as `APIStatusError` with the **200** status of the open response, so the status alone
   can't classify them); raw transport errors (`httpx2.TransportError`, `OSError`). Anything else
   (other 4xx, `TypeError`, `ValidationError`, …) is skipped at once with reason
   `non-retryable error before first delta: <Type>: <msg>`. It still never raises: the catch
   stays broad, and only the *retry* decision is narrow.
3. **Skipped turns return `text=""`** even after partial deltas, matching `run_turn` semantics.
   Consumers discard partial text on `turn_completed{skipped}` (KAN-17/19 contract). *(unchanged)*
4. **Aborted streams record usage** *(supersedes the old NOTE 4)*. On the timeout and mid-stream
   skip paths, `stream.current_message_snapshot.usage` is read best-effort. That property asserts
   before `message_start`, so the read is guarded and never raises. The tokens are charged via
   `guardrails.record_usage` and reported on the skipped `TurnResult` (`tokens_in`/`tokens_out`),
   keeping the PRD §8 per-debate cap honest when the service is degraded.
5. **No shared-helper refactor of `run_turn`**: the guardrail block is duplicated so `run_turn`
   stays provably untouched. *(unchanged)*
6. **`.env.example` not updated**: it documents only core keys. *(unchanged)*
7. **Hard per-turn budget** *(supersedes the old NOTE 7)*. Three mechanisms:
   - Each attempt's request timeout is the **remaining** budget, built with the SDK's re-exported
     `anthropic.Timeout(remaining, connect=min(5, remaining))`.
   - Once the stream is open, a **watchdog** (`threading.Timer(remaining, …)`, daemon, injectable
     `timer` factory like `sleep`/`clock`, always cancelled in `finally`) force-closes it at the
     deadline. That stops a stall and also a ping-only stream, which never trips a per-read
     timeout.
   - Backoff sleeps are capped at the remaining budget.

   **Finding during implementation:** `MessageStream.close()` from another thread does *not*
   interrupt a read blocked in `recv`. The probe showed the reader waiting out its full read
   timeout. So the watchdog first calls `socket.shutdown(SHUT_RDWR)` on the socket, reached via
   the response's standard `network_stream` extension, then `close()`. The blocked read then
   fails at once with `httpx2.ReadError`. When the watchdog fires it sets a flag, and the
   resulting error is classified as `timeout` **by that flag**, not by the clock. A fire that
   races a normal finish is a no-op: `stop()` runs under a lock before the timer is cancelled.
   Worst case: about the budget once the stream is open. Before response headers arrive, it is
   bounded by the per-phase request timeouts (roughly remaining + connect).
8. **Streaming is the only retry layer** *(supersedes the old NOTE 8)*. The stream call uses
   `self._client.with_options(max_retries=0)` (shares the HTTP client), so SDK retries no longer
   multiply the budget or the request count. A persistent 5xx now makes 3 HTTP requests per turn
   instead of 9. `run_turn`/`complete` keep the SDK default, unchanged.
9. **Timeout classification by cause** *(new; supersedes the clock-based GOTCHA)*. A failure is
   `timeout` when the watchdog fired, or when there is an `APITimeoutError` / `TimeoutError` /
   transport `TimeoutException` anywhere in the `__cause__`/`__context__` chain. The clock is only
   a last-resort fallback, and only for non-`APIError` exceptions, so a 5xx/429/overloaded keeps
   its real reason. The underlying exception is always logged (`error=%r`). If the budget runs
   out during retries, the loop-top check still returns `timeout`, but the last error is logged.
10. **Empty deltas are skipped** (`if not delta: continue`) and never reach `on_delta` (KAN-17:
    "producers should skip empty chunks"). `text == "".join(emitted deltas)` still holds.
11. **`turn_timeout_seconds = Field(60.0, gt=0)`**: a zero or negative value fails at startup
    instead of silently timing out every turn.
12. **Concurrency docstring softened**: "same properties as `run_turn`". `DebateGuardrails`
    check-then-record isn't atomic (already the case on `main`), so concurrent turns can overshoot
    the cap slightly. A lock plus a reservation in `guardrails.py` is a follow-up ticket, not
    this one.
13. **Transport import**: `import httpx2 as _transport`, falling back to `httpx` on
    `ImportError`. It's needed only to recognise raw transport exceptions, which the SDK leaks
    unwrapped from mid-stream reads. anthropic 1.2.0 hard-depends on httpx2, but
    `pyproject.toml` still allows `anthropic>=0.40`, whose older versions used httpx.

**Not a new DEC:** the "retry only before first delta" rule is the story's own acceptance
criterion (recorded in the epic spec's "Gaps and assumptions"), a behavioral detail of one method
rather than an architectural choice. No Accepted DEC is contradicted; DEC-012 (Proposed) is
irrelevant to this ticket.

**Out of scope:** orchestrator changes / event emission (KAN-19), SSE contract & event schemas
(KAN-17), run lifecycle / `/stream` endpoint / broker (B-T4, blocked on DEC-012), per-debate
timeout & keepalive (B-T5), judge streaming (DEC-007 keeps it non-streamed).

**Confidence score: 9/10** for one-pass implementation — the SDK behavior is verified against the
installed source; the main risk is fake-clock index semantics in the timeout tests.

**Post-review verification** (real `Anthropic` client with default `max_retries=2` against a
local fake SSE server, `TURN_TIMEOUT_SECONDS=1`): a hung request took 4.40 s with 3 requests and
now takes 1.00 s with 1. Pings for 4 s then text took 4.25 s and now take 1.01 s (`timeout`). One
delta then a stall now takes 1.01 s (`timeout`) with 11 tokens charged, where it used to charge 0.
A persistent 500 at a 30 s budget made 9 requests and now makes 3 (`llm error after 3 attempts`).
No `Timer` threads are left alive afterwards.
