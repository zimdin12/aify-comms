"""The existing start, stop, restart and delete entry points hand a DEFINED agent to the D9 lifecycle queue.

Asked before any legacy effect. An undefined agent answers None and keeps its legacy path until D8.
The lifetime the request must name comes only from what the agent's own host published (the G8 mirror),
never from a session id or a pid; the host checks it again before it acts.
"""
import uuid

from fastapi import HTTPException

from service.api_core import agent_lifecycle_requests as lifecycle
from service.api_core import partial_status_shadow as shadow
from service.api_core.definition_start import StartRefused, start_binding
from service.api_core.operator_authz import require_lifecycle
from service.clock import now
from service.lifecycle_models import LAUNCHING_ACTIONS


def _fresh_publishers(machine_id, view, at):
    return [p for (machine, _instance), p in view.items() if machine == machine_id and not p.unavailable
            and p.complete and shadow.age_class(p.data_applied_at, at) == 'fresh']


def published_lifetime(machine_id, agent_id, view, at):
    """The one lifetime the agent's host publishes for it now, or None when it runs nowhere there."""
    publishers = _fresh_publishers(machine_id, view, at)
    if not publishers:
        raise HTTPException(409, f'no fresh agent state from {machine_id}, so the running lifetime cannot be '
                                 'named; try again once its aify-env publishes')
    lifetimes = {row.lifetime for p in publishers for row in p.rows if row.agent_id == agent_id and row.lifetime}
    if len(lifetimes) > 1:
        raise HTTPException(409, 'its host publishes more than one running lifetime for this agent')
    return next(iter(lifetimes), None)


def stopped_by_operator(machine_id, agent_id, view, at):
    """True when the agent's host publishes it stopped by the operator: a message must not start it."""
    return any(row.agent_id == agent_id and row.state == 'stopped'
               for p in _fresh_publishers(machine_id, view, at) for row in p.rows)


async def owner_of(db, agent_id):
    held = await (await db.execute('SELECT machine_id FROM agent_definitions WHERE agent_id=?', (agent_id,))).fetchone()
    return held['machine_id'] if held else None


async def delegate(db, agent_id, action, actor, request, *, fresh_context=False):
    """Queue `action` for a defined agent and answer the receipt, or None for an undefined agent.
    Opens and commits its own transaction, so call it before the route writes anything."""
    if action in LAUNCHING_ACTIONS:
        # Refused at once, as before D9c: withdrawn, resident, or a definition its host cannot use.
        try:
            await start_binding(db, agent_id)
        except StartRefused as refused:
            raise HTTPException(409, str(refused))
    machine_id = await owner_of(db, agent_id)
    if machine_id is None:
        return None
    proof = require_lifecycle({'requestedBy': actor}, request)  # before anything about its state is said
    body = {'requestId': f'legacy-{uuid.uuid4().hex}', 'action': action, 'requestedBy': actor,
            'expectedLifetime': published_lifetime(machine_id, agent_id, shadow.mirror.view(), shadow.clock()),
            'freshContext': fresh_context}
    await db.execute('BEGIN IMMEDIATE')
    try:
        queued = await lifecycle.admit_lifecycle(db, agent_id, body, proof, now())
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    # QUEUED IS NOT DONE: the host executes it. Callers say so and point at the receipt.
    return {'ok': True, 'agentId': agent_id, 'action': action, 'queued': True, 'request': queued}
