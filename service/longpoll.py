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

from service.db import _is_lock_error
import asyncio
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


async def _wait_once(scope: str, timeout: float) -> None:
    """Block up to `timeout` seconds or until notify(scope)/notify('*')."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _waiters[scope].add(fut)
    try:
        await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        _waiters[scope].discard(fut)
        if not _waiters[scope]:
            _waiters.pop(scope, None)


async def longpoll(
    wait_ms: Optional[int],
    attempt: Callable[[], Awaitable[dict]],
    is_empty: Callable[[dict], bool],
    *,
    scope: str = GLOBAL_SCOPE,
    fallback_s: float = DEFAULT_FALLBACK_S,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    lock_result: Optional[dict] = None,
) -> dict:
    """Run `attempt()`; if its result is empty and `wait_ms` > 0, wait for work and
    retry until a non-empty result, the client disconnects, or `wait_ms` elapses.

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
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        if is_disconnected is not None:
            try:
                if await is_disconnected():
                    return result
            except Exception:
                pass
        await _wait_once(scope, min(remaining, fallback_s))
        result = await _try()
        if not is_empty(result):
            return result
