"""Is a spawn_request already standing behind this agent? One question, its own leaf.

v0.5.4. It went to `api_core/dispatch_start.py` first — the module whose subject is making a worker
exist — and that was a CYCLE: `dispatch_start` imports `managed_env`, and `managed_env` is one of this
function's readers. The import graph, not the subject taxonomy, decided where it could live. So it sits
below both, importing the standard library and `service.clock` -- itself a leaf with no service
dependencies, which is why reaching it costs nothing here -- and either may reach it.

Worth stating because "put it with the module that shares its subject" is right until it is not, and the
failure mode is an ImportError at app startup rather than a test failure.
"""

from __future__ import annotations

import time

from service.clock import ISO_SECONDS

#: How long a queued or claimed spawn_request is taken as evidence that a worker is coming.
#:
#: NOT A NEW NUMBER. `managed_env.SPAWN_INFLIGHT_WINDOW_SECONDS` is the same 300 seconds, bounding the
#: `starting`/`running` arm of `_has_pending_or_booting_spawn_request` for exactly this reason -- its
#: docstring says "so a stuck orphan never blocks future autostarts". It is duplicated here rather
#: than imported because importing `managed_env` from this module is the cycle the header describes,
#: and `test_a_stale_spawn_request_is_not_a_promise` asserts the two stay equal.
#:
#: That file also records what two independent numbers cost last time: 300 here and 180 there left a
#: window where the status said "idle, send something" while the code refused to start a worker.
SPAWN_CLAIM_WINDOW_SECONDS = 300


async def _has_claimable_spawn_request(db, agent_id: str) -> bool:
    """True when a RECENT queued/claimed spawn_request already backs this agent.

    A claimable spawn_request means a bridge will (or already did) spawn the worker, so the dispatch
    can safely sit queued instead of being rejected.

    AND THAT IS A CLAIM ABOUT THE FUTURE, which a row of any age cannot make. Unbounded, this answered
    "somebody is coming" from a request nobody had claimed in hours: every dispatch to that agent then
    sat queued behind a promise nothing was keeping, and the agent looked merely busy. The environment
    being down for five minutes on 2026-09-01 is enough to produce one, and a runtime nothing can
    launch produces one that never resolves at all.

    THE BOUND FIXES EVERY CAUSE, which is why it is here rather than in a reaper. A sweep that deletes
    stale requests has to tell "no environment can ever serve this" from "the environment is down for
    a moment", and it cannot -- a reaper with a short threshold would have destroyed legitimate queued
    spawns during that same outage. Freshness asks a question that is answerable: has anything touched
    this recently.

    Past the window the caller refuses the dispatch with an actionable message instead of queueing it,
    which is the failure this project prefers: one message that arrives as an error beats one that
    disappears into a queue.

    Its sibling `_has_pending_or_booting_spawn_request` bounds its `starting`/`running` arm and leaves
    the same queued/claimed arm unbounded. That is the identical gap, but it guards against DUPLICATE
    spawns rather than stranded dispatches, so relaxing it trades a permanent strand for a possible
    double worker -- a different decision, deliberately not made here.
    """
    cutoff = time.strftime(ISO_SECONDS, time.gmtime(time.time() - SPAWN_CLAIM_WINDOW_SECONDS))
    row = await (await db.execute(
        """
        SELECT id
        FROM spawn_requests
        WHERE agent_id = ?
          AND status IN ('queued','claimed')
          AND COALESCE(NULLIF(updated_at, ''), created_at) >= ?
        LIMIT 1
        """,
        (agent_id, cutoff),
    )).fetchone()
    return bool(row)
