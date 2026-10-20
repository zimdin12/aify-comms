"""The existing start, stop, restart and delete entry points hand a DEFINED agent to the D9 lifecycle queue.

Asked before any legacy effect. An undefined agent answers None; since D8 its caller refuses a start of it.
The lifetime the request must name comes only from what the agent's own host published (the G8 mirror),
never from a session id or a pid; the host checks it again before it acts.
"""
import uuid

from fastapi import HTTPException

from service.api_core import agent_lifecycle_requests as lifecycle
from service.api_core import partial_status_shadow as shadow
from service.api_core.report_age import legacy_receipt_age
from service.api_core.definition_start import StartRefused, start_binding
from service.api_core.operator_authz import require_lifecycle
from service.clock import now
from service.lifecycle_models import LAUNCHING_ACTIONS, OPEN_SQL


def _fresh_publishers(machine_id, view, at):
    return [p for (machine, _instance), p in view.items() if machine == machine_id and not p.unavailable
            and p.complete and legacy_receipt_age(p.data_applied_at, at) == 'fresh']  # SW5 moves this to report_age


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


def _new_request(machine_id, agent_id, action, actor, fresh_context):
    return {'requestId': f'legacy-{uuid.uuid4().hex}', 'action': action, 'requestedBy': actor,
            'expectedLifetime': published_lifetime(machine_id, agent_id, shadow.mirror.view(), shadow.clock()),
            'freshContext': fresh_context}


async def delegate(db, agent_id, action, actor, request, *, fresh_context=False, brief=''):
    """Queue `action` for a defined agent and answer the receipt, or None for an undefined agent. `brief` is the
    started worker's first message (JSON subject, body, priority). Opens and commits its own transaction, so
    call it before the route writes anything."""
    if action in LAUNCHING_ACTIONS:
        # Refused at once, as before D9c: withdrawn, resident, or a definition its host cannot use.
        try:
            await start_binding(db, agent_id)
        except StartRefused as refused:
            raise HTTPException(409, str(refused))
    machine_id = await owner_of(db, agent_id)
    if machine_id is None:
        # AN UNDEFINED AGENT'S DELETE IS STILL A DELETE: its caller removes it here, so the key that locks a defined
        # agent's delete locks this one too (pre-tag review of 0.9, F6).
        if action == 'delete':
            require_lifecycle({'requestedBy': actor, 'action': action}, request)
        return None
    proof = require_lifecycle({'requestedBy': actor, 'action': action}, request)  # before anything about its state is said
    await db.execute('BEGIN IMMEDIATE')
    try:
        # A REPEAT OF WHAT IS ALREADY WAITING is that request, not an error: a second Start clicked while the
        # first is still queued answered 409 "open lifecycle request". Another action waiting is still refused,
        # and so is a Recreate behind a waiting Restart: that restart keeps the conversation the caller asked to drop.
        # One carrying a brief is never a repeat: the waiting request would not deliver it.
        waiting = None if brief else await (await db.execute(
            f"SELECT id FROM agent_lifecycle_requests WHERE agent_id=? AND action=? AND fresh_context=? "
            f"AND status IN {OPEN_SQL}", (agent_id, action, int(fresh_context)))).fetchone()
        # Only a NEW request names the running lifetime, so a stale mirror refuses a new one and still answers a
        # repeat with the request already waiting (pre-tag review of 0.9, F4).
        queued = (await lifecycle.lifecycle_request_by_id(db, waiting['id']) if waiting
                  else await lifecycle.admit_lifecycle(db, agent_id, _new_request(machine_id, agent_id, action, actor,
                                                                                   fresh_context), proof, now(),
                                                       brief=brief))
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    # QUEUED IS NOT DONE: the host executes it. Callers say so and point at the receipt.
    return {'ok': True, 'agentId': agent_id, 'action': action, 'queued': True, 'request': queued,
            'alreadyQueued': waiting is not None}
