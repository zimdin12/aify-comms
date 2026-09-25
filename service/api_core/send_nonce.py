"""What a reused `clientNonce` on /messages/send means: the same send again, or a mistake.

A nonce names ONE logical send, so a bridge that hit a transient socket error can retry it and get
the original messageId back instead of a duplicate (#240). A retry carries the same payload. A
DIFFERENT payload under a nonce already used is a caller bug, and answering it as a replay drops the
new send while reporting ok:true -- which is what happened until 0.7.0, because the lookup keyed on
(from_agent, client_nonce) alone. It is refused with 409 now, so the caller finds out.

THE RETRY IDENTITY IS EVERYTHING THE SEND DECIDES, fingerprinted and stored on the message row:
type, subject, body, priority, the RESOLVED reply parent, the RESOLVED recipient set, whether it
triggers, and the delivery options (steer, queueIfBusy, requireReply) and origin. The first cut
compared only five of these, so a retry that changed the reply parent or the priority got the old
message back and the change was lost (v0.7 review). The delivery options cannot be read back from
the dispatch runs -- a run merges with a queued one and ORs its flags, and a steer makes a control,
not a run -- which is why the identity is stored rather than reconstructed.

A row written before the fingerprint existed has none, and is judged on the fields the message row
itself carries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

from fastapi import HTTPException


def _conflict(nonce: str, what: str) -> HTTPException:
    return HTTPException(
        409,
        f'clientNonce "{nonce}" was already used for a different message ({what}). '
        "A nonce names one logical send; use a fresh one for each new message.",
    )


def send_fingerprint(req, *, in_reply_to: Optional[str], recipients: Iterable[str]) -> str:
    """The identity of one logical send, from the values the route resolved rather than the raw ask."""
    canonical = {
        "type": str(req.type or ""),
        "subject": str(req.subject or ""),
        "body": str(req.body or ""),
        "priority": str(req.priority or "normal"),
        "inReplyTo": str(in_reply_to or ""),
        "recipients": sorted({str(r) for r in recipients}),
        "trigger": bool(req.trigger),
        "steer": req.steer,
        "queueIfBusy": bool(req.queueIfBusy),
        "requireReply": req.requireReply,
        "origin": str(req.origin or ""),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


async def prior_send_for_nonce(db, req, nonce: str, *, fingerprint: str, in_reply_to: Optional[str]) -> Optional[str]:
    """The messageId this nonce already produced, or None if it is unused. Raises 409 on a mismatch."""
    rows = await (await db.execute(
        "SELECT id, to_agent, type, subject, body, priority, in_reply_to, dispatch_requested, send_fingerprint "
        "FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC",
        (req.from_agent, nonce),
    )).fetchall()
    if not rows:
        return None
    first = rows[0]
    stored = str(first["send_fingerprint"] or "")
    if stored:
        if stored != fingerprint:
            raise _conflict(nonce, "another payload, recipient or delivery option")
        return first["id"]
    for field, wanted in (("type", req.type), ("subject", req.subject), ("body", req.body),
                          ("priority", req.priority or "normal"), ("in_reply_to", in_reply_to)):
        if str(first[field] or "") != str(wanted or ""):
            raise _conflict(nonce, f"another {field}")
    if req.to and req.to not in {row["to_agent"] for row in rows}:
        raise _conflict(nonce, "another recipient")
    triggered = int(bool(req.trigger))
    if any(int(row["dispatch_requested"] or 0) != triggered for row in rows if row["to_agent"] != "dashboard"):
        raise _conflict(nonce, "a triggered send" if req.trigger else "an untriggered send")
    return first["id"]
