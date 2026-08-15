"""Whether a dispatch is expected to come back with an answer.

RELOCATED, not rewritten, in v0.5.4 — both functions are byte-identical from
`service/routers/dispatch_messages/shared.py`, where they had two router importers and no reason to
live. They take no dependency beyond a type hint, which makes them layer-0 leaves that had been
sitting a layer too high.

THE MOVE WAS FORCED BY A REAL BLOCK rather than by tidiness. `send_message`'s dispatch-run creation
could not be extracted while these lived in a router: an api_core leaf importing from
`service.routers` is the cycle the layering exists to prevent. See
`service/api_core/console_input_queue.py`, which is the extraction this unblocked and is the only
api_core leaf that imports these two. (Until 2026-08-15 this line named a module that was never
created under that name, so the trail led nowhere; the dead name is not repeated here, because a
pointer nobody can follow is the whole defect.) This is the
second time in v0.5.4 that a small helper in a router's shared module blocked a real split; the first
was `_apply_status_event`.

THE TWO ARE SEPARATE ON PURPOSE. `_message_type_expects_reply` is the DEFAULT the message type
implies; `_dispatch_requires_reply` is what the caller actually asked for, with that default applied
only when the caller said nothing. Collapsing them would lose the difference between "did not ask"
and "asked for false", and a `requireReply=false` that silently became the type's default is the
shape of a run that waits forever for a reply nobody owes.
"""
from __future__ import annotations

from typing import Optional


def _dispatch_requires_reply(explicit: Optional[bool], *, default: bool) -> bool:
    if explicit is None:
        return bool(default)
    return bool(explicit)


def _message_type_expects_reply(message_type: str) -> bool:
    return (message_type or "").strip().lower() in {"request", "review", "error"}
