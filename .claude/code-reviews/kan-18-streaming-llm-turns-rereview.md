# Code Re-review: KAN-18 (EPIC-B, B-T2) Streaming persona turns in the LLM client

This is an independent pre-commit re-review of branch `KAN-18`. It covers the uncommitted changes against `main` (`ef1ff7d`).

The requirements were checked against four sources:
- Jira KAN-18.
- `docs/specs/epic-b-live-streaming.md` (B-T2).
- The Decision Log (Confluence 1015810, v12).
- The installed SDK source in `backend/.venv/lib/python3.14/site-packages/` (`anthropic==1.2.0`, `httpx2==2.12.0`, `httpcore2==2.12.0`, CPython 3.14.3 with the GIL enabled).

I formed my own assessment first. Only after that did I read the earlier review (`kan-18-streaming-llm-turns.md`) and the plan's NOTES.

**Stats:**

- Files Modified: 3 (`backend/app/core/config.py`, `backend/app/services/llm.py`, `backend/tests/test_llm.py`)
- Files Added: 0 source files. Two untracked process docs exist: `.claude/plans/kan-18-streaming-llm-turns.md` and `.claude/code-reviews/kan-18-streaming-llm-turns.md`.
- Files Deleted: 0
- New lines: 1225 (config 4, llm.py 525, tests 696)
- Deleted lines: 5

**Gate results** (run from `backend/` with the shared venv):

- `pytest`: 103 passed.
- `ruff format --check`: clean.
- `ruff check`: 3 errors, all of which already exist on `main`:
  - RUF100 in `tests/test_debates_api.py:10`
  - C408 in `tests/test_guardrails.py:7`
  - C408 in `tests/test_llm.py:32`, which is the old `make_guardrails` helper

  None of the three comes from this change.

**Acceptance criteria (KAN-18 / spec B-T2):**

| AC | Result |
| --- | --- |
| `on_delta` called for each delta, in order, and `text` equals the joined deltas | Met. Covered by the unit tests and by the real-SDK probe (`'Hello world'`). |
| A guardrail breach gives `skipped`, with no API call and no deltas | Met |
| A transient failure before the first delta is retried with backoff; a failure after deltas is not retried and gives `skipped` with a reason | Mostly met. Probes confirmed retries for a 529, an `overloaded_error` event, and a connection dropped before the first delta. After a delta, a mid-stream error or a drop gives `skipped`. **Exception:** a connect timeout is transient, but it is never retried (#1). |
| Per-turn timeout (`turn_timeout_seconds`) gives `skipped` with reason `timeout` | Met. Stall, ping-only and slow-delta streams all end at about the budget once the stream is open. The bound slips when response headers arrive late (#3). |
| Tokens are recorded and logged as in `run_turn`, and `run_turn` is unchanged | Met on the success path. `run_turn` and `complete` are byte-identical to `main`. Usage on aborted streams is under-counted (#4). |

**How the behavior was checked:** throwaway probes in the session scratchpad ran the real `LLMService.stream_turn` and a real `Anthropic` client against a local HTTP/1.1 chunked SSE server. One probe used an `httpx2.MockTransport` instead.

| Scenario | Budget | Elapsed | Requests | Result |
| --- | --- | --- | --- | --- |
| normal stream | 2 s | 0.02 s | 1 | ok, tokens (10, 7), pool connection IDLE and reused |
| `message_start` then pings only | 2 s | 2.01 s | 1 | skipped / `timeout`, 11 tokens charged, pool empty |
| `message_start` then a stall | 2 s | 2.00 s | 1 | skipped / `timeout` (`ReadTimeout`) |
| **headers after 1.5 s, then pings** | 2 s | **3.51 s** | 1 | skipped / `timeout` (#3) |
| error event after one delta | 2 s | 0.00 s | 1 | skipped / `stream interrupted after 1 deltas` |
| error event before any delta, then ok | 2 s | 0.06 s | 2 | ok, but only 17 tokens charged (the first attempt's 10 input tokens are missing, #4) |
| connection dropped after one delta | 2 s | 0.00 s | 1 | skipped / interrupted (`RemoteProtocolError`) |
| connection dropped before any delta, then ok | 2 s | 0.06 s | 2 | ok |
| 529, then ok | 2 s | 0.06 s | 2 | ok |
| 400 | 2 s | 0.00 s | 1 | skipped / non-retryable `BadRequestError` |
| 3 x 529 | 2 s | 0.11 s | 3 | skipped / `llm error after 3 attempts` (was 9 requests before the fix) |
| **`ConnectTimeout` on attempt 1, then ok** | 60 s | **0.00 s** | **1** | **skipped / `timeout`, never retried** (#1) |
| 3 concurrent turns via `asyncio.to_thread` on a shared service, guardrails and pool: 1 pinging, 2 slow ok | 1 s | 1.06 s | 3 | 1 timeout and 2 ok (the ok turns are unaffected), 0 `Timer` threads left |
| `MessageStream.close()` alone from another thread during a stall | read 4 s | 4.02 s | 1 | the author's claim holds: `close()` does not unblock `recv` |
| same, ping-only stream, watchdog uses `close()` only (no shutdown) | 2 s | 3.84 s | 1 | a `close()`-only watchdog is **not** enough, so the socket shutdown is needed |
| **forced interleaving: watchdog fire begins after turn A has finished and released its connection; turn B reuses it** | 30 s | n/a | 2 | **turn B (innocent) is skipped: `stream interrupted … incomplete chunked read`** (#2) |
| same interleaving, `_force_close` returns early when `stream.response.is_closed` | 30 s | n/a | 2 | turn B ok. The ping-only turn still ends in 2.01 s. |

No exceptions reached `threading.excepthook` in any probe.

---

## Findings (most severe first)

```
severity: medium
file: backend/app/services/llm.py
line: 94-99, 609-613 (test that encodes it: backend/tests/test_llm.py:458-471)
issue: A connect timeout before the first delta ends the turn as `timeout` and is never retried, even with most of the budget left, although it is a transient failure that AC #3 says must be retried.
detail: Each attempt uses `Timeout(remaining, connect=min(5.0, remaining))`. A connect timeout at 5 s surfaces from `__enter__` as `APITimeoutError` (`_base_client.py` `_attempt_request`: `except httpx2.TimeoutException -> APITimeoutError`). `_classify_failure` tests `_is_timeout_error(exc)` before `_is_transient_error`, so the error becomes a terminal `timeout`. Probe: `ConnectTimeout` on attempt 1 with a 60 s budget gave `skipped / timeout` after 1 request and 0.00 s. The next attempt would have succeeded. The same applies to a pool timeout. This is a regression introduced by the review fixes. Before them, the SDK's own retries (`_should_retry_exception` retries `APITimeoutError`, a subclass of `APIConnectionError`) retried connect timeouts inside `__enter__`. `with_options(max_retries=0)` removed that layer, and "classify timeout by type" made connect timeouts terminal. The read, write and pool timeouts are all `remaining`, so any timeout other than connect only fires at or near the deadline anyway.
suggestion: Decide timeout by the budget, not by type: `watchdog_fired or (self._clock() >= deadline and not isinstance(exc, APIStatusError))`. An `APITimeoutError` raised with budget left then falls through to `_is_transient_error`, which already returns True for it, so it is retried. This also lets you delete `_exception_chain` and `_is_timeout_error` (and `test_stream_turn_timeout_detected_in_exception_chain`). Update `test_stream_turn_timeout_before_first_delta_is_not_retried` to assert that a connect timeout with budget left is retried, and that an `APITimeoutError` with the clock at the deadline gives `timeout`.
```

```
severity: medium
file: backend/app/services/llm.py
line: 137-151, 174-184, 529-531
issue: The watchdog can shut down the socket of a pooled connection that turn A has already released and another concurrent turn is using. So "a late fire is a no-op" does not hold, and the mechanism is not safe for other streams on the shared pool.
detail: The lock only orders `_fire` against `stop()`. The connection, however, is released before `stop()` runs. The SDK's `Stream.__stream__` closes the response in its `finally` block as soon as the body ends (`_streaming.py`). `HTTP11Connection._response_closed` then marks the connection IDLE and returns it to the pool. After that the reader still runs `get_final_message()` and only then calls `watchdog.stop()`. If the timer thread passes the `_stopped` check in that window, or is simply pre-empted between releasing the lock and `sock.shutdown`, it calls `shutdown(SHUT_RDWR)` through `stream.response.extensions["network_stream"]`. That object is the connection's live stream, not a per-response handle, so the call reaches a socket that may now carry another persona's request. In the orchestrator, the other two persona turns run concurrently on the same `Anthropic` client and pool (`with_options` shares `http_client`). The forced-interleaving probe reproduced it: innocent turn B was skipped with `incomplete chunked read`. The natural window is small: the fire must land within about a GIL switch interval (5 ms) of A finishing, near its deadline, and a concurrent checkout must happen in that window. So it is rare, not theoretical. If the connection is merely idle, the damage heals itself, because `has_expired()` sees a readable socket and discards it. Under HTTP/2 (not the default, and `h2` is not installed) the same `network_stream` is shared by every multiplexed stream, so every concurrent turn would die. Introduced by this change. Note that holding the lock across `_force_close` would **not** fix this, because the release happens inside the SDK before `stop()`.
suggestion: Minimal fix, verified by probe (the innocent turn stays ok, the ping-only turn still ends in 2.01 s): in `_fire`, under the lock, return early when `self._stream.response.is_closed`. httpx2 `Response.close()` sets `is_closed` before it releases the connection, so a completed response is never torn down. A narrower check-then-act gap remains, so say so in the docstring. The simplest alternative is to drop the watchdog and document that a ping-only stall is bounded by B-T5's per-debate timeout (see #5). Also add a test where `_fire` runs after the response is closed and assert that `get_extra_info` or `shutdown` is not called.
```

```
severity: low
file: backend/app/services/llm.py
line: 488, 515
issue: The watchdog is armed with `remaining` computed before the request was sent, so the hard bound is "budget + time-to-headers", not "about the budget" as the docstring (lines 382-386) and plan NOTE 7 claim.
detail: The `remaining` value computed at line 488 covers the connect, request and response-headers phases. The watchdog then starts a fresh `remaining`-second timer once `__enter__` returns. Probe: headers at 1.5 s followed by pings, with a 2 s budget, ended after 3.51 s. The per-delta clock check still cuts text-producing streams at the deadline, so only ping-only or stalled streams after slow headers overshoot. Introduced by this change. The unit test `test_stream_turn_each_attempt_gets_only_the_remaining_budget` asserts the interval but cannot see this, because the fake `__enter__` does not advance the clock.
suggestion: `watchdog = _StreamWatchdog(stream, max(0.0, deadline - self._clock()), self._timer)`. Add a test whose `__enter__` advances the fake clock by 10 s and assert that the timer interval is 50 s.
```

```
severity: low
file: backend/app/services/llm.py
line: 118-134, 635-636
issue: Usage on aborted streams is under-counted. Output tokens are only the `message_start` value (usually 1) until `message_delta` arrives at the end, and input tokens billed by a retried attempt are never recorded.
detail: `accumulate_event` (`lib/streaming/_messages.py`) updates `usage.output_tokens` only on `message_delta`, which the API sends after the content. So a turn cut off mid-text reports `tokens_out=1` (probe: `mid_error` gave (10, 1)). Separately, when an attempt fails after `message_start` but before the first text delta and is retried, `_classify_failure` raises `_RetryableStreamError` before `_skipped_with_usage`, so its input tokens are dropped (probe: `pre_error, ok` charged 17 instead of 27). The docstring (lines 366-367) says "Tokens already billed on an aborted stream are recorded". This is best-effort, so the severity is low. It is the PRD §8 cap that is affected.
suggestion: Charge `_partial_usage(stream)` before raising `_RetryableStreamError`. For output, add a rough estimate from the characters emitted (for example `max(snapshot_out, len("".join(chunks)) // 4)`), or soften the docstring to "input tokens and any reported output".
```

```
severity: low
file: backend/app/services/llm.py
line: 19-26, 58-64, 78-81, 137-185, 190-212, 469-693 (and backend/tests/test_llm.py:154-841)
issue: The change is large for the ticket. `llm.py` grows from 242 to 765 lines, and 696 test lines are added for one method. Several pieces can go without losing any acceptance criterion.
detail: Concretely:
  (a) `_exception_chain` and `_is_timeout_error`, about 16 lines plus one test, become unnecessary once #1 classifies timeouts by budget.
  (b) The `httpx` fallback import (lines 23-26) is dead code in this venv, because anthropic 1.2.0 hard-depends on httpx2. Raising the pin in `pyproject.toml` to `anthropic>=1.2` and importing `httpx2` directly is simpler.
  (c) `_stream_attempt` lines 488-497 repeat the loop-top deadline check at line 414, which runs immediately before. Keep `remaining = deadline - self._clock()` and drop the branch.
  (d) `self._client.with_options(max_retries=0)` builds a new client object on every attempt. Build it once in `__init__` (for example `self._stream_client`).
  (e) `_classify_failure`, `_timeout_result` and `_skipped_with_usage` pass the same 6-9 keyword arguments (model, tier, guardrails, round_number, persona_index, deltas) through every call site, about 60 lines of plumbing. A small frozen `_TurnCtx` dataclass, or closures inside `stream_turn`, would halve it.
  (f) The watchdog stack (`_TimerLike`, `TimerFactory`, `_daemon_timer`, `_StreamWatchdog`, `_force_close`, the `timer` injectable, the `fired` plumbing and about 9 tests) exists only for ping-only or trickle streams. Stalls are already bounded by the read timeout (`remaining`), and text-producing streams by the per-delta clock check. It is also the source of #2 and #3. The AC is met without it, so keep it only if B-T5 must rely on a hard per-turn bound. If it stays, apply the fixes from #2 and #3.
  Test overlap: `test_stream_turn_retries_error_raised_before_any_delta_during_iteration` duplicates the parametrised `OSError` case, and `test_default_watchdog_timer_is_a_daemon_thread` only tests `threading.Timer`.
suggestion: Apply (a)-(e) regardless; they are about 90 lines and pure simplification. Decide (f) explicitly: either keep the watchdog with the #2/#3 fixes, or drop it and add a sentence to B-T5 in the spec saying it provides the outer bound for ping-only stalls.
```

```
severity: low
file: backend/app/services/llm.py
line: 66-73, 102-115, 449-452
issue: With SDK retries disabled, the stream path no longer honors the server's `retry-after` and `x-should-retry` headers, which the SDK retry loop used.
detail: `SyncAPIClient._should_retry` (`_base_client.py:821`) obeys `x-should-retry: true/false` before looking at the status code, and `_sleep_for_retry` honors `retry-after`. Our loop retries on the status code alone with fixed 0.5 s and 1 s backoff. So on a 429 we may retry sooner than the server asked, and on a 5xx marked `x-should-retry: false` we retry anyway. The budget caps the damage. Introduced by this change (`run_turn` and `complete` still get the SDK behavior).
suggestion: In `_is_transient_error`, check `exc.response.headers.get("x-should-retry")` first. Optionally use `min(retry-after, remaining)` as the backoff. Alternatively, accept this for MVP and record it in the docstring.
```

```
severity: low
file: docs/architecture.md
line: 33, 42, 44, 161-170 (Change Log)
issue: Architecture drift is still unresolved: the doc does not mention `stream_turn` or `turn_timeout_seconds`.
detail: Line 44 lists only `run_turn` and `complete`. It should add `stream_turn`: streaming persona turns on the Sonnet tier, retry only before the first delta and only for transient errors, SDK retries off, skip with partial text discarded after deltas, a per-turn budget with a watchdog, usage charged on abort, and a sync `on_delta` called on the worker thread. Line 42 should mention `turn_timeout_seconds`. Line 33 ("no streaming") could add "`stream_turn` available (KAN-18); engine wiring in KAN-19". The Change Log needs a `KAN-18 · DEC-007` row. Per DEC-011, update the Confluence mirror (page 917506) in lockstep. This is applied at commit time (`commit` skill), not in this review.
suggestion: At `/commit`, update lines 33, 42 and 44, add the Change Log row, and mirror the changes to Confluence 917506.
```

Pre-existing issues (not introduced here, not counted above):
- The 3 ruff errors listed under Gate results.
- `DebateGuardrails.check` followed by `record_usage` is not atomic under the orchestrator's 3 concurrent threads. The docstring now says "same properties as `run_turn`", which is accurate. The follow-up ticket is still needed.

Checked and found correct:
- Thread-safety of `LLMService` itself: its state is read-only after `__init__`, and each attempt gets its own watchdog.
- The shared httpcore2 pool, which is thread-locked.
- `Timer` cleanup: 0 threads left after the runs.
- Timer threads are daemons.
- Mid-stream `error` events arrive as `APIStatusError` with status 200 and are classified by `body.error.type`.
- Raw `httpx2` transport errors from mid-stream reads are caught.
- A `break` at the deadline closes the connection. Nothing leaks, and the pool is empty afterwards.
- `on_delta` raising is contained and not retried.

Not probed: one theoretical CPython hazard. Closing an fd from another thread while a reader is between `poll` and `recv` could let a reused fd number be read. `_force_close` does this after the shutdown. Both the current design and the guarded variant share it.

---

## Decision drift (Decision Log, page 1015810 v12)

- **No Accepted DEC is contradicted.** DEC-007 is honored: `tier` defaults to `"personas"`, which resolves to `claude-sonnet-5`, and the tests assert this. The judge stays non-streamed, and `complete` and `run_turn` are unchanged. DEC-003, DEC-004 and DEC-008 are not touched. DEC-012 (Proposed) does not apply to B-T2.
- **No new DEC is needed.** The retry and skip policy is the story's own AC and is recorded in the spec's "Gaps and assumptions". The watchdog and socket teardown is an implementation detail of one method. One thing should be written down, though. If #5(f) keeps the soft bound (no watchdog), note it on B-T5 in the spec, because B-T5 must then be the outer bound for ping-only stalls.
- **Commit trailer:** `Decisions: DEC-007`. Updating DEC-007's "Implemented by" with `KAN-18 · <sha>` is optional, because routing is unchanged.

---

## Earlier review: resolution of its findings

| # | Earlier finding (severity) | Status now | Evidence |
| --- | --- | --- | --- |
| 1 | SDK retries multiply the timeout; about 3x budget, 9 requests (high) | **Resolved, with a regression** | `with_options(max_retries=0)` and a per-attempt `Timeout(remaining, connect=min(5, remaining))` are in place. Probe: 3 requests for a persistent 529 (was 9), and a stall ends at 2.00 s. The regression is that connect timeouts are no longer retried at all (new #1). |
| 2 | Deadline only checked on text deltas; pings defeat it (medium) | **Resolved, with two new defects** | The watchdog plus socket shutdown ends a ping-only stream at 2.01 s. However, it arms with a stale interval (new #3, 3.51 s with late headers), and it can tear down another stream's reused connection (new #2). |
| 3 | Aborted streams record no usage (medium) | **Partially resolved** | The timeout and mid-stream skip paths now charge snapshot usage (probe: 11 tokens charged). Output is still under-counted, and retried attempts charge nothing (new #4). |
| 4 | Errors after the deadline mislabelled `timeout` (low) | **Resolved, but over-corrected** | A 5xx or overloaded error past the deadline keeps its real reason (test and code), and the cause is logged with `%r`. Classifying by type also turned a transient connect timeout into a terminal `timeout` (new #1). |
| 5 | Non-transient pre-delta errors retried (low) | **Resolved** | `_is_transient_error` handles this, and the probe showed a 400 skipped after 1 request. A minor gap: `x-should-retry` is ignored (new #6). |
| 6 | `turn_timeout_seconds` not validated (low) | **Resolved** | `Field(60.0, gt=0)`, with a test for 0 and -1. |
| 7 | Docstring overstated concurrency safety (low) | **Resolved** | The docstring now says "same properties as `run_turn`", and the non-atomic cap is acknowledged. |
| 8 | Empty deltas forwarded (low) | **Resolved** | `if not delta: continue`, covered by `test_stream_turn_skips_empty_deltas`. |
| 9 | Fakes can't surface SDK interactions; missing backoff-past-deadline test (low) | **Mostly resolved** | Tests were added for the backoff deadline, retries being disabled, the shrinking timeout, and usage on skip. `_force_close` is still tested only with `MagicMock`. There are no tests for the late-fire-after-release case or the late-headers interval (see #2, #3). |
| 10 | `docs/architecture.md` drift (low) | **Open (expected at commit)** | Doc lines 33, 42 and 44 and the Change Log are unchanged (new #7). |

Plan NOTES checked as claims:
- NOTE 7 says "A fire that races a normal finish is a no-op". It is only partly true (new #2).
- NOTE 7 says "Worst case: about the budget once the stream is open". This is true only when measured from when the headers arrive, not from the start of the turn (new #3).
- NOTE 9 says the classification is by cause, and it is. But it makes connect timeouts terminal (new #1).
- NOTE 4 says "keeping the PRD §8 cap honest". It is partly honest (new #4).
- NOTE 13 (the httpx fallback) is dead code in this venv (#5b).
- The finding that `close()` alone does not interrupt `recv` is correct. I reproduced it: 4.02 s for close-only on a stall, and 3.84 s for a close-only watchdog on a ping-only stream.

## Verdict

**Ready after fixes.** Fix #1 (retry connect timeouts: decide timeout by budget, not type) and #2 (guard `_fire` with `response.is_closed`, or drop the watchdog). #3 is a one-line fix. #4-#6 are low. #7 is handled at `/commit`.
