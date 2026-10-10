"""Lifecycle wire fields. Core validates values without coercion."""
from typing import Any
from pydantic import BaseModel

#: The lifecycle actions that launch a worker, and so prepare a launch and are refused at once for a
#: definition that cannot start.
LAUNCHING_ACTIONS = ('start', 'restart', 'spawn')
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
