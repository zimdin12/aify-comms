"""What a reused `clientNonce` on /messages/send means: the same send again, or a mistake.

A nonce names ONE logical send, so a bridge that hit a transient socket error can retry it and get
the original messageId back instead of a duplicate (#240). A retry carries the same payload. A
DIFFERENT payload under a nonce already used is a caller bug, and answering it as a replay drops the
new send while reporting ok:true -- which is what happened until 0.7.0, because the lookup keyed on
(from_agent, client_nonce) alone. It is refused with 409 now, so the caller finds out.

THE RETRY IDENTITY IS EVERYTHING THE CALLER ASKED FOR, fingerprinted and stored on the message row:
type, subject, body, priority, the reply parent as given, the addressee as given (`to` or `toRole`),
whether it triggers, and the delivery options (steer, queueIfBusy, requireReply) and origin. The first
cut compared only five of these, so a retry that changed the reply parent or the priority got the old
message back and the change was lost (v0.7 review). 0.7.0 then fingerprinted the RESOLVED recipients
and parent, so a `toRole` retry after another agent took that role, or after the parent was deleted,
resolved differently and was refused although the first attempt had succeeded (v0.7.1 review, W07).
What the caller asked is fixed across attempts; what it resolves to is not.

The delivery options cannot be read back from the dispatch runs -- a run merges with a queued one and ORs its flags, and a steer makes a control,
not a run -- which is why the identity is stored rather than reconstructed.

A row written before the fingerprint existed has none, and is judged on the fields the message row
itself carries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from fastapi import HTTPException


def _conflict(nonce: str, what: str) -> HTTPException:
    return HTTPException(
        409,
        f'clientNonce "{nonce}" was already used for a different message ({what}). '
        "A nonce names one logical send; use a fresh one for each new message.",
    )


def send_fingerprint(req) -> str:
    """The identity of one logical send: what the caller asked for, not what it resolved to."""
    canonical = {
        "type": str(req.type or ""),
        "subject": str(req.subject or ""),
        "body": str(req.body or ""),
        "priority": str(req.priority or "normal"),
        "inReplyTo": str(req.inReplyTo or ""),
        "to": str(req.to or ""),
        "toRole": str(req.toRole or ""),
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
        "FROM messages WHERE from_agent = ? AND client_nonce = ? ORDER BY nonce_primary DESC, timestamp ASC, id ASC",
        (req.from_agent, nonce),
    )).fetchall()
    if not rows:
        return None
    first = rows[0]
    stored = str(first["send_fingerprint"] or "")
    if stored:
        if stored != fingerprint:
            raise _conflict(nonce, "another payload, recipient or delivery option")
        return _logical_send_id(first, rows)
    for field, wanted in (("type", req.type), ("subject", req.subject), ("body", req.body),
                          ("priority", req.priority or "normal"), ("in_reply_to", in_reply_to)):
        if str(first[field] or "") != str(wanted or ""):
            raise _conflict(nonce, f"another {field}")
    if req.to and req.to not in {row["to_agent"] for row in rows}:
        raise _conflict(nonce, "another recipient")
    triggered = int(bool(req.trigger))
    if any(int(row["dispatch_requested"] or 0) != triggered for row in rows if row["to_agent"] != "dashboard"):
        raise _conflict(nonce, "a triggered send" if req.trigger else "an untriggered send")
    return _logical_send_id(first, rows)


def _logical_send_id(first, rows) -> str:
    """The id the first attempt answered with. A fan-out answers `msg_id` and stores `msg_id-<recipient>`
    per row, so a retry that returned a row id handed back an id the caller had never seen (v0.7.1
    review, W09). `first` is the row that reserved the nonce, which is the first recipient's."""
    row_id = str(first["id"])
    suffix = f"-{first['to_agent']}"
    if len(rows) > 1 and row_id.endswith(suffix):
        return row_id[: -len(suffix)]
    return row_id
