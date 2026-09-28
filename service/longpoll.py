"""In-process long-poll support for the bridge "claim" endpoints.

Background
----------
The host bridges discover work by SHORT-polling a handful of "is there anything
for me yet?" endpoints on tight timers (`/dispatch/claim` every 3s,
`/terminals/controls/claim` every 800ms, etc.). With ~12 live bridges that is the
bulk of the service's request volume (~40 req/s) — each request opens a SQLite
connection, and `/dispatch/claim` even takes a `BEGIN IMMEDIATE` write lock on
every attempt. See DECISIONS.md, "Read GET endpoints must not run repair-WRITES
on the poll path" and the follow-on long-poll entry.

What this provides
------------------
A tiny, lock-free notification bus plus a `longpoll()` helper that converts those
short-polls into long-polls **without changing claim semantics**:

* The claim handler's existing body is reused verbatim as the per-attempt
  function. Calling it repeatedly server-side is identical to the bridge calling
  it repeatedly over HTTP — same atomic claim, same competition, same supersession
  and grace-window logic. The only behavioural change is that an EMPTY result is
  awaited (up to `wait_ms`) instead of returned immediately.
* `notify(scope)` is fired at the points where claimable work is enqueued, so a
  waiting claim wakes instantly when real work arrives.
* A per-iteration fallback timeout bounds latency to today's poll interval even if
  a `notify()` is ever missed — so missing an enqueue hook can only ever degrade to
  the current behaviour, never lose or further delay work. This is the property
  that makes the change safe to ship incrementally.

Single-worker only: the bus is process-global, consistent with `_LIVE_STATE_CACHE`
and the hard single-uvicorn-worker constraint (see CLAUDE.md / DECISIONS.md).
"""

from __future__ import annotations

from service.db_errors import _is_lock_error
import asyncio
import contextlib
import contextvars
import time
from collections import defaultdict
from typing import Awaitable, Callable, Optional

# Per-scope sets of pending waiter futures. A waiter registers its own future, so
# multiple concurrent waiters on the same scope are each resolved independently
# (no shared Event set/clear race).
_waiters: dict[str, set[asyncio.Future]] = defaultdict(set)

# Default per-iteration fallback: how long a waiter sleeps before re-attempting the
# claim when no notify() arrives. Keep at/under the legacy poll intervals so latency
# never regresses (dispatch short-poll was 3s; terminal-control was 800ms).
DEFAULT_FALLBACK_S = 3.0

# Hard ceiling on how long the server will hold a single long-poll request open,
# regardless of the client-requested wait. Kept safely BELOW the bridge's HTTP claim timeout
# (~28s) so the server always returns first — otherwise a high waitMs could hold the connection
# past the client deadline and surface as a spurious "claim timed out" on the bridge. Pairs with
# the short claim busy_timeout so the final per-iteration attempt can't overshoot this by >~1.2s.
MAX_WAIT_S = 25.0

# TIME SPENT DELIBERATELY WAITING, so a diagnostic can tell it from time spent working.
#
# `SLOW-REQ` warned on any request over 1000ms. A long poll HOLDS THE CONNECTION OPEN ON PURPOSE, so
# every one of them tripped it: measured over six hours on the operator's live service, 14,062
# SLOW-REQ lines of which 10,587 (75.3%) were `/claim` long-polls, and
# `/api/v1/environments/controls/claim` had a MINIMUM of 20,002ms -- not one of its 1,020 lines was a
# genuine slow request. The lines that mattered (`/api/v1/agents` reaching 5,578ms) were buried under
# them, in the one log the debug skill tells an operator to read.
#
# A MUTABLE HOLDER RATHER THAN A PLAIN ContextVar VALUE, and the difference is load-bearing.
# Starlette's BaseHTTPMiddleware runs the downstream app in its own task, which COPIES the context --
# so a value `set()` below the middleware is invisible above it. The copy shares the holder's
# reference, so mutating the object does propagate. The middleware also keeps its own reference, which
# makes the read independent of the contextvar surviving at all.
_WAITED: contextvars.ContextVar = contextvars.ContextVar("aify_longpoll_waited", default=None)


def begin_wait_accounting() -> dict:
    """Start counting deliberate waiting for this request. Returns the holder to read later."""
    holder = {"ms": 0.0}
    _WAITED.set(holder)
    return holder


def note_waited(seconds: float) -> None:
    """Record time this request spent asleep waiting for work, never time spent doing it."""
    holder = _WAITED.get()
    if holder is None:
        return
    holder["ms"] += max(0.0, float(seconds)) * 1000.0


def attributable_ms(total_ms: int, holder: Optional[dict]) -> int:
    """How much of a request's wall time is WORK rather than deliberate waiting.

    Pure, so the threshold decision can be tested without a server. Clamped at zero: a holder that
    somehow out-counts the request is a bug in the accounting, and a negative duration would read as
    a very fast request rather than as the fault it is.
    """
    waited = float((holder or {}).get("ms", 0.0) or 0.0)
    return max(0, int(round(float(total_ms) - waited)))


# Wildcard scope: a waiter on "*" wakes on every notify; a notify("*") wakes everyone.
GLOBAL_SCOPE = "*"



def notify(scope: str = GLOBAL_SCOPE) -> int:
    """Wake every waiter registered on `scope` (and every wildcard waiter).

    Synchronous and never raises — safe to call from any enqueue path, including
    inside a DB write transaction. Returns the number of waiters woken (for tests).
    """
    woken = 0
    targets = {scope, GLOBAL_SCOPE} if scope != GLOBAL_SCOPE else set(_waiters.keys())
    for s in targets:
        for fut in list(_waiters.get(s, ())):
            if not fut.done():
                fut.set_result(None)
                woken += 1
    return woken


async def _wait_once(scope: str, timeout: float, gone: Optional[asyncio.Event] = None) -> None:
    """Block up to `timeout` seconds, until notify(scope)/notify('*'), or until the caller has `gone`."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _waiters[scope].add(fut)
    left = asyncio.ensure_future(gone.wait()) if gone is not None else None
    try:
        await asyncio.wait({fut} if left is None else {fut, left}, timeout=timeout,
                           return_when=asyncio.FIRST_COMPLETED)
    finally:
        if left is not None:
            left.cancel()
        _waiters[scope].discard(fut)
        if not _waiters[scope]:
            _waiters.pop(scope, None)


@contextlib.asynccontextmanager
async def _watch_for_the_caller_leaving(request):
    """An event set when the caller of `request` disconnects, for as long as the block runs.

    `request.is_disconnected()` cannot see it: the app is wrapped in `BaseHTTPMiddleware` subclasses,
    under which it always answers False (v0.7.1 review S2, found on `/listen`; the claim routes kept the
    same call until the 0.7.6 review, O1). Awaiting `receive()` does reach the `http.disconnect`. The
    claim body has already been read by the time the wait starts, so nothing else is reading it.
    No request (a direct unit call) means no caller to watch, and the event is never set.
    """
    gone = asyncio.Event()
    if request is None:
        yield gone
        return

    async def watch() -> None:
        while True:
            message = await request.receive()
            if message.get("type") == "http.disconnect":
                gone.set()
                return

    watcher = asyncio.create_task(watch())
    try:
        yield gone
    finally:
        watcher.cancel()


async def longpoll(
    wait_ms: Optional[int],
    attempt: Callable[[], Awaitable[dict]],
    is_empty: Callable[[dict], bool],
    *,
    scope: str = GLOBAL_SCOPE,
    fallback_s: float = DEFAULT_FALLBACK_S,
    request=None,
    lock_result: Optional[dict] = None,
) -> dict:
    """Run `attempt()`; if its result is empty and `wait_ms` > 0, wait for work and
    retry until a non-empty result, the client disconnects, or `wait_ms` elapses.

    `request`: the HTTP request being held. Its caller leaving ends the wait, and no attempt runs
    after that, so a caller that has gone is never handed work it will not receive: a claim marks
    what it returns, and a return to nobody would strand it. An attempt already running when the
    caller leaves still commits; that window is the attempt's own duration, not the wait's.

    `attempt` must be a COMPLETE, self-contained claim (open its own connection /
    transaction) so it is safe to call repeatedly. `is_empty(result)` decides whether
    to keep waiting — return False for any actionable result (a claimed run, a
    `stopped`/`release` signal, a non-empty control list, …) so it returns at once.

    `lock_result`: when set, a transient SQLite lock/busy contention raised by `attempt()`
    is treated as this (empty) result instead of bubbling to a 503. A claim that can't grab
    the write lock just means "nothing claimed this round" — the caller retries on the next
    poll. The attempt's own connection is already closed in its `finally`, so this never leaks.
    """
    async def _try():
        try:
            return await attempt()
        except Exception as exc:  # noqa: BLE001 — only lock/busy is swallowed; everything else re-raises
            if lock_result is not None and _is_lock_error(exc):
                return lock_result
            raise

    result = await _try()
    wait_ms = int(wait_ms or 0)
    if wait_ms <= 0 or not is_empty(result):
        return result

    deadline = time.monotonic() + min(wait_ms / 1000.0, MAX_WAIT_S)
    async with _watch_for_the_caller_leaving(request) as gone:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or gone.is_set():
                return result
            # ONLY THE SLEEP is counted as waiting. The retry `attempt()` below is real work and must
            # keep counting against the slow-request threshold, or a claim that is slow to EXECUTE would
            # hide inside a long poll -- which is the failure this accounting exists to make visible, not
            # a second place to bury it.
            slept_at = time.monotonic()
            await _wait_once(scope, min(remaining, fallback_s), gone)
            note_waited(time.monotonic() - slept_at)
            # Asked AFTER the wake and BEFORE the attempt: the wake is usually the work arriving, and
            # claiming it now would mark it for a caller that will never read the answer.
            if gone.is_set():
                return result
            result = await _try()
            if not is_empty(result):
                return result
