"""Is a spawn_request already standing behind this agent? One question, its own leaf.

v0.5.4. It went to `api_core/dispatch_start.py` first — the module whose subject is making a worker
exist — and that was a CYCLE: `dispatch_start` imports `managed_env`, and `managed_env` is one of this
function's readers. The import graph, not the subject taxonomy, decided where it could live. So it sits
below both, importing nothing but the standard library, and either may reach it.

Worth stating because "put it with the module that shares its subject" is right until it is not, and the
failure mode is an ImportError at app startup rather than a test failure.
"""

from __future__ import annotations


async def _has_claimable_spawn_request(db, agent_id: str) -> bool:
    """True when a queued/claimed spawn_request already backs this agent.

    A claimable spawn_request means a bridge will (or already did) spawn the
    worker, so the dispatch can safely sit queued instead of being rejected.
    """
    row = await (await db.execute(
        "SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed') LIMIT 1",
        (agent_id,),
    )).fetchone()
    return bool(row)
