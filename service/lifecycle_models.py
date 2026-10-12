"""Lifecycle wire fields. Core validates values without coercion."""
from typing import Any
from pydantic import BaseModel

#: The lifecycle actions that launch a worker, and so prepare a launch and are refused at once for a
#: definition that cannot start.
LAUNCHING_ACTIONS = ('start', 'restart', 'spawn')
#: Every lifecycle action a request may ask for.
ACTIONS = ('start', 'stop', 'restart', 'kill', 'spawn', 'delete')
#: THE OPERATOR KEY IS AN OPTIONAL LOCK ON WHAT CANNOT BE UNDONE (Steven, 2026-10-11): when one is configured,
#: deleting an agent needs it. A kill is the host's stop without the durable stop mark, so a killed agent
#: starts again (aify-env `AgentLifecycle.#stop`); anything but a delete the API key may ask, as its caller.
IRREVERSIBLE_ACTIONS = ('delete',)
API_KEY_ACTIONS = tuple(action for action in ACTIONS if action not in IRREVERSIBLE_ACTIONS)
#: The statuses a request is still waiting in, a lifecycle one's or a definition's. One per agent at a time.
OPEN_STATUSES = ('pending', 'claimed')
OPEN_SQL = "(" + ", ".join(f"'{status}'" for status in OPEN_STATUSES) + ")"
#: A lifecycle request's settled statuses: the host reported it, and the result is immutable.
TERMINAL = ('done', 'refused', 'failed')


class LifecycleSubmit(BaseModel):
    requestId: str
    action: Any = None
    requestedBy: Any = None
    expectedLifetime: Any = None
    expectedRevision: Any = None
    freshContext: Any = None


class LifecycleClaim(BaseModel):
    bridgeId: Any = None
    machineId: Any = None


class LifecycleAttachment(LifecycleClaim):
    terminalId: Any = None
    handle: Any = None
    processId: Any = None
    lifetime: Any = None
    cols: Any = None
    rows: Any = None


class LifecycleResult(LifecycleClaim):
    status: Any = None
    outcome: Any = None
    resultLifetime: Any = None
    finishedAt: Any = None
    resultIncarnation: Any = None
    resultRevision: Any = None
