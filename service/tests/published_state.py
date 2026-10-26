"""What a host's aify-env published about its agents, put in the service's mirror for one test.

The lifecycle delegation (D9c) names the running lifetime only from this mirror, so a test that drives an
existing start, stop, restart or delete of a DEFINED agent publishes first, as the host would.
"""
from service.api_core import agent_state_mirror as state_mirror


def publish(test, machine_id, rows, *, instance="default", applied_at=None):
    """Publish `rows` ({agentId: (lifetime, state)}) from `machine_id` and restore the mirror after `test`."""
    before = state_mirror.mirror._view
    test.addCleanup(setattr, state_mirror.mirror, "_view", before)
    state_mirror.mirror.feed(state_mirror.Projection(
        (machine_id, instance), "snapshot", 1, applied_at or state_mirror.clock(),
        tuple(state_mirror.Record(agent_id, lifetime, state) for agent_id, (lifetime, state) in rows.items()), ()))
