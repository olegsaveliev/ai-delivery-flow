# Code Review: KAN-18 (EPIC-B, B-T2) Streaming persona turns in the LLM client

Branch `KAN-18`, uncommitted changes against `main` (`ef1ff7d`).
Requirements checked against: Jira KAN-18, `docs/specs/epic-b-live-streaming.md` (B-T2), the Decision Log (Confluence 1015810, v11), and the installed `anthropic==1.2.0` SDK source (`backend/.venv/lib/python3.14/site-packages/anthropic/`).
Plan `.claude/plans/kan-18-streaming-llm-turns.md` was read as the author's intent, not as ground truth.

**Stats:**

- Files Modified: 3 (`backend/app/core/config.py`, `backend/app/services/llm.py`, `backend/tests/test_llm.py`)
- Files Added: 1 (`.claude/plans/kan-18-streaming-llm-turns.md`, untracked)
- Files Deleted: 0
- New lines: 536 in code and tests (+698 in the plan file)
- Deleted lines: 3

**Gate results (run from `backend/` with the shared venv):**

- `pytest`: 70 passed.
- `ruff format --check`: clean.
- `ruff check`: 3 errors. All 3 already exist on `main` (RUF100 in `tests/test_debates_api.py:10`, C408 in `tests/test_guardrails.py:7` and `tests/test_llm.py:28`). None comes from this change.

**Acceptance criteria (KAN-18 / spec B-T2):**

| AC | Result |
| --- | --- |
| `on_delta` called per delta, in order; `text` equals the joined deltas | Met (tests + real-SDK probe) |
| Guardrail breach gives `skipped`, no API call, no deltas | Met |
| Retry before the first delta; no retry after deltas (`skipped`, reason recorded) | Met. Retries are layered on top of the SDK's own retries (see #1). Non-transient errors are retried too (#5). |
| Per-turn timeout gives `skipped` with reason `timeout` | Partly met. The reason is correct, but the budget is not enforced as a bound (#1, #2), and some non-timeout errors are reported as `timeout` (#4). |
| Tokens recorded and logged like `run_turn`; `run_turn` unchanged | Met on success. `run_turn` is byte-identical. Aborted streams record nothing (#3). |

**How the behavior was checked:** throwaway probes in the session scratchpad ran `stream_turn` with a **real** `Anthropic` client (default `max_retries=2`, as in production) against a local fake SSE server, with `turn_timeout_seconds=1.0`:

| Server behavior | Elapsed | HTTP requests | Result |
| --- | --- | --- | --- |
| normal stream | 0.02 s | 1 | ok, `tokens_spent=15` |
| accepts request, never responds | **4.40 s** | **3** | skipped / `timeout` |
| same, client `max_retries=0` | 1.00 s | 1 | skipped / `timeout` |
| HTTP 500 every time | 1.21 s | 3 | skipped / **`timeout`** (the real cause was a 500) |
| HTTP 500 every time, 30 s budget | 3.77 s (app backoff stubbed) | **9** | skipped / `llm error after 3 attempts` |
| `message_start` + pings for 4 s, then text | **4.25 s** | 1 | skipped / `timeout` |
| one delta, then the stream stalls | 1.02 s | 1 | skipped / `timeout`, **`tokens_spent=0`** although `message_start` reported 10 input tokens |

---

## Findings (most severe first)

```
severity: high
file: backend/app/services/llm.py
line: 320-326 (also docstring 220-224, and the loop at 251)
issue: The per-turn timeout is not a bound. The SDK's built-in retries inside `__enter__` multiply it, so a hung request takes about 3x the budget, not the "about 2x" the docstring promises.
detail: `self._client` is built with the SDK default `max_retries=2`. `MessageStreamManager.__enter__` sends the request through `SyncAPIClient.request` (`_base_client.py:1111-1156`). That loop retries `APITimeoutError` / `APIConnectionError` (`_should_retry_exception`, line 856) and 408/409/429/5xx (`_should_retry`, line 821). It also sleeps between retries, honoring `retry-after` up to 60 s. Each SDK attempt gets the full `timeout=self._turn_timeout`. Passing a float also raises the SDK's default connect timeout from 5 s to 60 s (`_constants.py:9`). So a request that never gets response headers returns `APITimeoutError` only after about 3 x 60 s plus backoff, which is about 3 minutes at the default budget. The probe measured 4.40 s against a 1.0 s budget (3 HTTP requests). With `max_retries=0` it took 1.00 s (1 request). The same layering turns a persistent 5xx into 9 HTTP requests per turn (3 app attempts x 3 SDK attempts; probe confirmed). Later app attempts also get the full budget as their per-read timeout, not what is left of it. The layering pattern already exists in `run_turn` on `main`, where no wall-clock budget is promised. This change introduces the broken timeout guarantee and the incorrect docstring. The plan (NOTES 7 and 8) notes the double retry but does not work out its effect on the timeout.
suggestion: Let the app loop be the only retry layer for streaming. Call `self._client.with_options(max_retries=0).messages.stream(...)`, or build a second client once in `__init__` with `max_retries=0`. Give each attempt the remaining budget, e.g. `timeout=httpx2.Timeout(remaining, connect=min(5.0, remaining))`, or a float `remaining` if you want to avoid importing httpx2. Then fix the docstring's worst-case statement. Add a test that asserts the stream call is made with retries disabled and with a shrinking per-attempt timeout.
```

```
severity: medium
file: backend/app/services/llm.py
line: 327-333
issue: The deadline is checked only when a text delta arrives. Non-text traffic, such as SSE `ping` events, keeps the per-read timeout from firing, so a turn can run past its budget with nothing to stop it.
detail: `Stream.__stream__` (`_streaming.py`) silently drops `ping` events, and `text_stream` yields only `text_delta`s. Any bytes on the socket reset httpx's per-read timeout, so neither the SDK nor this loop sees time pass. The probe (pings for 4 s, then text) finished after 4.25 s against a 1.0 s budget. A stream that pings and then ends with no text would even return `ok` long after the deadline. The spec relies on the per-turn timeout (B-T2) as the inner layer under the B-T5 per-debate timeout, so it should be a real bound. Introduced by this change (the plan calls the bound about 2x and says a watchdog is "not worth it").
suggestion: Enforce a hard deadline. Start a `threading.Timer(remaining, stream.close)` right after `__enter__` and cancel it in `finally`. When a close forced by the timer causes the exception, classify it as `timeout`. If you choose not to do this for MVP, record that choice explicitly: fix the docstring and note in the spec that B-T5 must not rely on the per-turn bound.
```

```
severity: medium
file: backend/app/services/llm.py
line: 330-333, 339-362
issue: Streams that end early (timeout, or a failure after deltas were emitted) record no usage, so tokens that were billed are never charged against the PRD §8 per-debate cost cap.
detail: A stream that is cut off after producing text has already been billed for its input tokens and for the output tokens generated so far. Only the success path calls `guardrails.record_usage`. `MessageStream.current_message_snapshot.usage` is available as soon as `message_start` arrives (`lib/streaming/_messages.py:459`); it carries `input_tokens` then, and `output_tokens` once a `message_delta` arrives. The probe (one delta, then a stall) returned `tokens_spent=0` although `message_start` had reported 10 input tokens. This matters most exactly when the service is degraded: timeouts are the common abort path, and each one lets up to `max_tokens` plus the full prompt escape the cap. Introduced by this change. Plan NOTE 4 chose this knowingly, but it trades away the "cost guardrails must not regress" requirement stated in the plan's own Problem Statement.
suggestion: In the timeout and mid-stream-skip paths, make a best-effort read of `stream.current_message_snapshot.usage`: record `input_tokens`, and either the reported `output_tokens` or a conservative estimate from the characters received. Guard the read with a try, because the snapshot is `None` before `message_start`. Return those numbers in the skipped `TurnResult` as well, so logs show the real spend.
```

```
severity: low
file: backend/app/services/llm.py
line: 343
issue: Any error raised after the deadline has passed is reported as `reason="timeout"`, even when the cause was a 500, a 429 or a connection reset.
detail: `if isinstance(exc, APITimeoutError) or self._clock() >= deadline` classifies by clock, not by cause. Because the SDK's own retry sleeps (#1) can run past the deadline, a persistent 5xx or 429 comes back labelled `timeout`. The probe reproduced this: HTTP 500 at a 1 s budget gave `reason='timeout'`. `_timeout_result` also never logs the underlying exception, so the real cause is lost from the logs. Introduced by this change.
suggestion: After #1, classify by type: `APITimeoutError`, `TimeoutError`, or an `httpx2.TimeoutException` found anywhere in the exception's `__cause__` chain. Use the clock check only as a fallback. Pass `exc` to `_timeout_result` so it appears in the warning.
```

```
severity: low
file: backend/app/services/llm.py
line: 339-348
issue: Every failure before the first delta is retried, including non-transient ones (400/401/403/404/413 `APIStatusError`, and `TypeError`/`ValidationError` from bugs in our own code), although the AC says "transient".
detail: `run_turn` already retries every `APIError` on `main`, so the 4xx part is not new. But `except Exception` → `_RetryableStreamError` extends retries to programming errors, which fail identically 3 times. That burns backoff time out of the turn budget and hides a bug behind "llm error after 3 attempts". Catching broadly is still correct for the "never raises" contract. Only the retry decision is too broad.
suggestion: Treat as retryable only `APIConnectionError`, `APIStatusError` with status 408/409/429/5xx (also covers the mid-stream `error` SSE event, e.g. overloaded), and raw transport errors (`httpx2.TransportError`, `OSError`). Anything else should skip at once with its reason, still without raising.
```

```
severity: low
file: backend/app/core/config.py
line: 33
issue: `turn_timeout_seconds` is not validated. A value of 0 or less (for example a typo in `.env`) makes every streamed turn time out immediately and silently.
detail: `deadline = clock() + 0` fails the check at the top of the loop, so every persona turn is `skipped`/`timeout` without any API call or error. httpx would also receive `timeout=0`. Introduced by this change.
suggestion: Declare it as `turn_timeout_seconds: float = Field(60.0, gt=0)` (pydantic `Field`).
```

```
severity: low
file: backend/app/services/llm.py
line: 208-209 (docstring); root cause in backend/app/core/guardrails.py:37-62
issue: The docstring claims "Safe to call concurrently … sharing one guardrails object", but `DebateGuardrails` check-then-record is not atomic.
detail: The orchestrator runs the 3 persona turns of a round concurrently through `asyncio.to_thread`. All 3 `check(estimated_tokens=max_tokens)` calls finish before any `record_usage`, so a round can overshoot `max_tokens_per_debate` by up to 2 x (prompt + max_tokens). `tokens_spent += …` is an unsynchronized read-modify-write. It is rare under the GIL (the venv's Python 3.14.3 has the GIL enabled) but not guaranteed. This already exists on `main` with `run_turn`; this change does not make it worse. The new docstring states a guarantee that does not hold.
suggestion: Soften the docstring now ("same concurrency properties as `run_turn`"). In a follow-up ticket, add a `threading.Lock` to `DebateGuardrails` and reserve `estimated_tokens` in `check`, then reconcile the reservation in `record_usage`.
```

```
severity: low
file: backend/app/services/llm.py
line: 327-337
issue: Empty text deltas are passed to `on_delta` unchanged. The sibling KAN-17 contract (`TurnDeltaEvent.text`) says "producers should skip empty chunks".
detail: `text_stream` yields `chunk.delta.text` as-is, and the API can send an empty `text_delta`. This is not a bug in KAN-18 alone, but it is the point where the two tickets meet. KAN-19 would otherwise emit empty `turn_delta` frames. Otherwise the sibling ticket is compatible: `TurnStatus` values `ok`/`skipped` match `TurnResult.status`, and `TurnCompletedEvent.content` ("empty for skipped turns") matches `text=""` on skip. KAN-17 does not touch `config.py`, so there is no merge conflict.
suggestion: Add `if not delta: continue` before the deadline check (it costs nothing and keeps `text == "".join(deltas)` true), or record the filtering as a KAN-19 responsibility.
```

```
severity: low
file: backend/tests/test_llm.py
line: 163-223, 238-423
issue: The fakes cannot surface the SDK interactions above, and one edge case listed in the plan has no test.
detail: `FakeStreamManager` replaces the whole client, so SDK-level retries, the per-attempt timeout, pings and partial usage (#1-#3) cannot be exercised. Plan "Edge Cases" lists "deadline passes during backoff → next attempt-start check returns timeout", but no test covers the timeout check at the top of the loop (llm.py:252-253). There is also no test for a pre-delta 4xx or programming error (#5).
suggestion: Add the backoff-past-deadline test (use `sleep=lambda s: clock.__setattr__("now", clock.now + 61)`). Assert the stream kwargs or options for retries disabled and remaining-budget timeout (after #1). Add a skip test that asserts usage is recorded (after #3).
```

```
severity: low
file: docs/architecture.md
line: 43-44, 33, 161-170 (Change Log)
issue: Architecture drift. The `app/services/llm.py` row does not mention `stream_turn`, and the config row does not mention the per-turn timeout.
detail: Line 44 lists only `run_turn` / `complete`. It should add `stream_turn` (streaming persona turns on the Sonnet tier; retry only before the first delta; skip, discarding partial text, after deltas; per-turn `timeout`; sync `on_delta` on a worker thread) with the tag KAN-18. Line 43 should mention the resilience knobs (`llm_max_retries`, backoff, `turn_timeout_seconds`). Line 33 ("no streaming") is still true for the engine, since nothing calls `stream_turn` yet, but could say "`stream_turn` available (KAN-18); engine wiring in KAN-19". The Change Log needs a `KAN-18 · DEC-007` row. Per DEC-011, update the Confluence mirror (page 917506) in lockstep. This is applied at commit time (`commit` skill), not in this review.
suggestion: At `/commit`: update rows 43/44 and line 33, add the Change Log row, and mirror all of it to Confluence 917506.
```

---

## Decision drift (Decision Log, page 1015810 v11)

- **No Accepted DEC is contradicted.**
  - DEC-007 is honored. `tier` defaults to `"personas"` and resolves through `model_for` to `claude-sonnet-5`, which the tests assert. The judge stays non-streamed and `complete` is untouched.
  - DEC-003, DEC-004 and DEC-008 are not affected by this layer.
  - DEC-012 (Proposed) does not apply to B-T2.
- **No new architectural decision needs a DEC.** Two policies were introduced: "retry only before the first delta" and "a skipped turn returns `text=""`; consumers discard partial deltas". Both are story-level behavior already recorded in the spec's "Gaps and assumptions" and matched by KAN-17's `turn_completed` contract.
  - If #2 is resolved by accepting a soft per-turn bound instead of adding a watchdog, write that down in the spec or on B-T5, because B-T5 relies on the bound.
- **Commit trailer:** `Decisions: DEC-007`. Adding `KAN-18 · <sha>` to DEC-007's "Implemented by" is optional, since this ticket realizes nothing new for routing. The plan's open question stands.

## Plan choices that are themselves questionable

- **NOTE 7 ("worst case about 2x").** Wrong once the SDK's retries are counted: it is about 3x or more for a hung request, and unbounded for traffic that contains no text (#1, #2).
- **NOTE 8 ("double retry layering, not changed").** Acceptable for `run_turn`, but for streaming it both breaks the timeout promise and multiplies requests to 9 (#1).
- **NOTE 4 ("no usage for aborted streams").** Contradicts the plan's own "must not regress the cost guardrails". The partial snapshot the plan mentions as the alternative is cheap to use (#3).
- **NOTE 2 ("any pre-delta exception is transient").** Catching broadly is needed for "never raises", but that does not require *retrying* broadly (#5).
- **GOTCHA "any transport timeout implies the deadline passed".** True for a raw read timeout, but the reverse inference used in code (the clock is past the deadline, so it must be a timeout) mislabels other errors (#4).

## Verdict

**Ready after fixes.** Fix #1 (disable SDK retries for the stream call and give each attempt the remaining budget) before committing. #2 and #3 should be fixed or explicitly accepted in writing. The rest are low.
