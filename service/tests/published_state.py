"""What a host's aify-env published about its agents, put in the service's mirror for one test.

The lifecycle delegation (D9c) names the running lifetime only from this mirror, so a test that drives an
existing start, stop, restart or delete of a DEFINED agent publishes first, as the host would.
"""
from service.api_core import partial_status_shadow as shadow


def publish(test, machine_id, rows, *, instance="default", applied_at=None):
    """Publish `rows` ({agentId: (lifetime, state)}) from `machine_id` and restore the mirror after `test`."""
    before = shadow.mirror._view
    test.addCleanup(setattr, shadow.mirror, "_view", before)
    shadow.mirror.feed(shadow.Projection(
        (machine_id, instance), "snapshot", 1, applied_at or shadow.clock(),
        tuple(shadow.Record(agent_id, lifetime, state) for agent_id, (lifetime, state) in rows.items()), ()))
