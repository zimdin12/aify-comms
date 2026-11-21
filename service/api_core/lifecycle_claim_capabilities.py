"""Observed lifecycle claim capabilities and why a queued automatic start waits."""
import json
from collections import defaultdict

from service.api_core.serialization import _json_loads_or
from service.lifecycle_models import AUTOMATIC_START_CAPABILITY, AUTOMATIC_START_INTENT


def declared_capabilities(value):
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def supports_automatic_start(value):
    return AUTOMATIC_START_CAPABILITY in declared_capabilities(value)


async def record_claim(db, environment, capabilities):
    """Replace the observation after a proven claim, even when it claimed no rows."""
    observation = {
        'machineId': environment['machine_id'], 'bridgeId': environment['bridge_id'],
        'capabilities': declared_capabilities(capabilities),
    }
    encoded = json.dumps(observation)
    if encoded != environment['lifecycle_claim']:
        await db.execute('UPDATE environments SET lifecycle_claim=? WHERE id=?', (encoded, environment['id']))


def _capability_observations(rows):
    """Ignore observations from an environment's previous owner or bridge."""
    seen = defaultdict(list)
    for row in rows:
        claim = _json_loads_or(row['lifecycle_claim'], {})
        if (isinstance(claim, dict) and claim.get('machineId') == row['machine_id']
                and claim.get('bridgeId') == row['bridge_id']):
            seen[row['machine_id']].append(supports_automatic_start(claim.get('capabilities')))
    return seen


async def waiting_reasons(db, agent_ids):
    """Read current queued-start reasons in a batch. Never turn lack of a claim into evidence of an old host."""
    ids = tuple(dict.fromkeys(agent_ids))
    if not ids:
        return {}
    rows = await (await db.execute(
        f'''SELECT l.agent_id, e.machine_id, e.bridge_id, e.lifecycle_claim
            FROM agent_lifecycle_requests l JOIN environments e ON e.machine_id=l.machine_id
            WHERE l.agent_id IN ({','.join('?' for _ in ids)}) AND l.start_intent=?
              AND l.status='pending' AND e.status='online' ''', (*ids, AUTOMATIC_START_INTENT))).fetchall()
    seen = _capability_observations(rows)
    return {row['agent_id']: _wait_reason(row['machine_id']) for row in rows
            if seen[row['machine_id']] and not any(seen[row['machine_id']])}


def _wait_reason(machine_id):
    return (f'the start waits for aify-env with {AUTOMATIC_START_CAPABILITY} on {machine_id}; '
            'restart aify-env on 32c7dc42 or later')


async def mark_waiting_runs(db, runs):
    """Annotate admitted, non-steered sends without changing their status."""
    queued = [run for run in runs if not run.get('steered')]
    reasons = await waiting_reasons(db, (run['targetAgentId'] for run in queued))
    for run in queued:
        if reason := reasons.get(run['targetAgentId']):
            run['waitingReason'] = reason
    return list(dict.fromkeys(reasons.values()))
